import hashlib
import logging
import os
import re

import boto3
import json
import dotenv
import phonenumbers
from gdolim import GoogleSheetsClient
from botocore.exceptions import ClientError
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

dotenv.load_dotenv()

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))

HEADER_BOT_STATUS = 'סטטוס בוט'
STATUS_ISSUE = 'הנפקה'
STATUS_ISSUE_DONE = 'הנפקה - בוצע'
STATUS_ISSUE_ERROR = 'הנפקה - שגיאה'

INVALID_DUCATI_MEMBER_CODE_PLACEHOLDER = 'חסר רישום'

EMAIL_ADDRESS_SENDER = "Ducati Israel <noreply@docil.co.il>"
SMS_SENDER_ID = 'Ducati'

GOOGLE_SPREADSHEET_ID = os.environ['GOOGLE_SPREADSHEET_ID']
GOOGLE_SERVICE_ACCOUNT_CREDENTIALS = os.environ['GOOGLE_SERVICE_ACCOUNT_CREDENTIALS']
AWS_S3_BUCKET_NAME = os.environ['AWS_S3_BUCKET_NAME']
AWS_ACCESS_KEY_ID = os.environ['AWS_ACCESS_KEY_ID']
AWS_SECRET_ACCESS_KEY = os.environ['AWS_SECRET_ACCESS_KEY']
AWS_SNS_REGION = os.environ['AWS_SNS_REGION']
AWS_SES_REGION = os.environ['AWS_SES_REGION']

aws_session = boto3.Session(aws_access_key_id=AWS_ACCESS_KEY_ID, aws_secret_access_key=AWS_SECRET_ACCESS_KEY)
aws_s3_resource = aws_session.resource('s3')
aws_ses_client = aws_session.client('ses', region_name=AWS_SES_REGION)
aws_sns_client = aws_session.client('sns', region_name=AWS_SNS_REGION)

google_service_account_credentials = json.loads(bytes.fromhex(GOOGLE_SERVICE_ACCOUNT_CREDENTIALS).decode())
google_sheets_client = GoogleSheetsClient(google_service_account_credentials, GOOGLE_SPREADSHEET_ID)


def normalize_phone_number(phone_number, default_country_code="IL"):
    try:
        phone_number = phonenumbers.parse(phone_number, default_country_code)
        return f'+{phone_number.country_code}{phone_number.national_number}'
    except phonenumbers.NumberParseException:
        logging.exception(f'failed normalizing phone number "{phone_number}"')
        return ''


def normalize_email_address(email_address):
    return email_address.lower().strip()


EMAIL_TEMPLATE_SUCCESS = '''
היי {{hebrew_full_name}},
שמחים שהצטרפת למועדון דוקאטי!
הונפק לך כרטיס חבר דיגיטלי בכתובת {{card_url}}
אנא שמור על כתובת זו במועדפים.
אנא הצג כרטיס זה בעת קבלת שירות או טיפול או רכישת אביזרים כדי לקבל את ההטבות המגיעות לחברי המועדון.
שים לב לתוקף הרשום על הכרטיס: בגלל נהלי דוקאטי, שנת החברות תמיד מסתיימת בסוף אוקטובר של השנה המופיעה על הכרטיס.
'''


def send_sms(phone_number, sms_message, sender_id=SMS_SENDER_ID):
    number = normalize_phone_number(phone_number)
    aws_sns_client.publish(PhoneNumber=number, Message=sms_message, MessageAttributes={'AWS.SNS.SMS.SenderID': {'DataType': 'String', 'StringValue': sender_id}, 'AWS.SNS.SMS.SMSType': {'DataType': 'String', 'StringValue': 'Transactional'}})


SMS_TEMPLATE_SUCCESS = '''
היי {{hebrew_full_name}},
שמחים שהצטרפת למועדון דוקאטי!
הונפק לך כרטיס חבר דיגיטלי
{{card_url}}
'''


def send_email(email_subject, email_text, recipient_email_address, sender_email_address=EMAIL_ADDRESS_SENDER, reply_to_email_address=None):
    email_text_html = email_text.replace('\n', '<br>')
    email_text_html = f'<html><head></head><body><p dir="rtl">{email_text_html}</p></body></html>'

    email_message = MIMEMultipart('mixed')
    email_message['Subject'] = email_subject
    email_message['From'] = sender_email_address
    email_message['To'] = recipient_email_address
    if reply_to_email_address:
        email_message['Reply-To'] = reply_to_email_address

    email_message_body = MIMEMultipart('alternative')

    email_message_body_plain = MIMEText(email_text, 'plain', "utf-8")
    email_message_body.attach(email_message_body_plain)

    email_message_body_html = MIMEText(email_text_html, 'html', "utf-8")
    email_message_body.attach(email_message_body_html)

    email_message.attach(email_message_body)
    email_message.add_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
    email_message.add_header('Cache - Control', 'post - check = 0, pre - check = 0')
    email_message.add_header('Pragma', 'no-cache')
    try:
        aws_ses_client.send_raw_email(
            Source=sender_email_address,
            Destinations=[recipient_email_address],
            RawMessage={
                'Data': email_message.as_string(),
            }
        )

    except ClientError:
        logging.exception('could not send email')


def main():
    logging.basicConfig(level=logging.INFO)
    google_sheets_client.reload()

    for index, item in enumerate(google_sheets_client.items):
        try:
            email_address = item['כתובת אימייל']
            if not email_address:
                logging.debug(f'skipping line {index}, empty phone')
                continue

            email_address = normalize_email_address(email_address)

            phone_number = item['מספר טלפון סלולרי']
            if not phone_number:
                logging.debug(f'skipping line {index}, empty phone')
                continue

            phone_number = normalize_phone_number(phone_number)

            membership_year = item['חברות'].strip()
            ducati_member_code = item['קוד דוקאטי'].strip()
            role = item['תפקיד'].strip()
            hebrew_full_name = item['שם מלא בעברית'].strip()
            english_full_name = item['שם מלא באנגלית'].strip()
            motorcycle_model = item['דגם אופנוע נוכחי'].strip()
            registration_type = 'בזוג' if item['רישום יחיד או בזוג'].lower().strip() == 'y' else 'יחיד'
            revoked = item['עזב'].lower().strip() in ['y', 'rip']
            vcard_id = f'{email_address}:{phone_number}'
            vcard_id = vcard_id.encode()
            vcard_id = hashlib.sha1(vcard_id).hexdigest()
            short_vcard_id = vcard_id[:10]

            if not re.match(r'^\d+$', ducati_member_code):
                ducati_member_code = INVALID_DUCATI_MEMBER_CODE_PLACEHOLDER

            bot_status = item.get(HEADER_BOT_STATUS, '')
            if bot_status == STATUS_ISSUE:
                vcard_info = {
                    "hebrew_full_name": hebrew_full_name,
                    "english_full_name": english_full_name,
                    "membership_year": membership_year,
                    "ducati_member_code": ducati_member_code,
                    "role": role,
                    "motorcycle_model": motorcycle_model,
                    "registration_type": registration_type,
                }

                if revoked:
                    vcard_info['revoked'] = revoked
                    logging.info(f'revoked card #{index}')

                logging.info(f'{short_vcard_id} {vcard_info}')

                vcard_info = json.dumps(vcard_info)
                vcard_info = vcard_info.encode('utf-8')
                aws_s3_resource.meta.client.put_object(Body=vcard_info, Bucket=AWS_S3_BUCKET_NAME, Key=f'card/{vcard_id}.json', ACL='public-read')

                short_url_info = json.dumps({
                    'id': vcard_id
                })
                short_url_info = short_url_info.encode('utf-8')
                aws_s3_resource.meta.client.put_object(Body=short_url_info, Bucket=AWS_S3_BUCKET_NAME, Key=f'short/{short_vcard_id}.json', ACL='public-read')

                short_card_url = f'https://card.docil.co.il/#/{short_vcard_id}'
                sms_message = SMS_TEMPLATE_SUCCESS.replace('{{hebrew_full_name}}', hebrew_full_name).replace('{{card_url}}', short_card_url)
                send_sms(phone_number, sms_message)

                # TODO enable send email message
                email_message = EMAIL_TEMPLATE_SUCCESS.replace('{{hebrew_full_name}}', hebrew_full_name).replace('{{card_url}}', short_card_url)
                email_subject = 'מועדון דוקאטי ישראל - כרטיס חבר וירטואלי'
                # send_email(email_subject, email_message, email_address)

                google_sheets_client.set_item_field(item, HEADER_BOT_STATUS, STATUS_ISSUE_DONE)
        except:
            logging.exception(f'failed issuing card for line #{index}')
            google_sheets_client.set_item_field(item, HEADER_BOT_STATUS, STATUS_ISSUE_ERROR)


if __name__ == '__main__':
    main()

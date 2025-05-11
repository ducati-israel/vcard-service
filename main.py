import datetime
import hashlib
import logging
import os
import re
import tempfile
import time
import boto3
import json
import dotenv
import phonenumbers
from gdolim import GoogleSheetsClient
from botocore.exceptions import ClientError
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from applepassgenerator.client import ApplePassGeneratorClient
from applepassgenerator.models import Generic

dotenv.load_dotenv()

APPLE_CARD_TEAM_IDENTIFIER = "E85N35G3YB"
APPLE_CARD_PASS_TYPE_IDENTIFIER = "pass.com.madappgang.doc.israel"
APPLE_CARD_ORGANIZATION_NAME = "DOC Israel"
APPLE_CARD_PKPASS_PRIVATE_KEY = os.environ['APPLE_CARD_PRIVATE_KEY']
APPLE_CARD_PKPASS_PRIVATE_KEY = APPLE_CARD_PKPASS_PRIVATE_KEY.replace('\\n', '\n')
APPLE_CARD_PKPASS_PRIVATE_KEY_PASSWORD = os.environ['APPLE_CARD_PRIVATE_KEY_PASSWORD']

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))

HEADER_BOT_STATUS = 'סטטוס בוט'
HEADER_LAST_RENEWAL_REMINDER_DATE = 'תאריך תזכורת חידוש אחרון'
STATUS_UPDATE = 'עדכון'
STATUS_UPDATE_TYPO = 'עידכון'
STATUS_ISSUE = 'הנפקה'
STATUS_DONE = 'בוצע'
STATUS_ERROR = 'שגיאה'

EMAIL_SUBJECT_ISSUE = 'מועדון דוקאטי ישראל - כרטיס חבר וירטואלי'
EMAIL_SUBJECT_RENEWAL_REQUEST = 'מועדון דוקאטי ישראל - בקרוב תפוג החברות שלך במועדון'
EMAIL_ADDRESS_SENDER = "Ducati Israel <noreply@docil.co.il>"

SMS_SENDER_ID = 'DOCIL'

GOOGLE_SPREADSHEET_ID = os.environ['GOOGLE_SPREADSHEET_ID']
GOOGLE_SERVICE_ACCOUNT_CREDENTIALS = os.environ['GOOGLE_SERVICE_ACCOUNT_CREDENTIALS']
AWS_S3_BUCKET_NAME = os.environ['AWS_S3_BUCKET_NAME']
AWS_ACCESS_KEY_ID = os.environ['AWS_ACCESS_KEY_ID']
AWS_SECRET_ACCESS_KEY = os.environ['AWS_SECRET_ACCESS_KEY']
AWS_SNS_REGION = os.environ['AWS_SNS_REGION']
AWS_SES_REGION = os.environ['AWS_SES_REGION']
CONTACT_PHONE_NUMBER = os.environ['CONTACT_PHONE_NUMBER']

RATE_LIMIT_SLEEP_INTERVAL_SECONDS = 5
RENEWAL_NOTIFICATIONS_PERIOD_DAYS = 31
RENEWAL_NOTIFICATIONS_TTL_DAYS = 15
MAX_DOCUMENT_UPDATES = 25

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


def send_sms(phone_number, sms_message, sender_id=SMS_SENDER_ID):
    return # disabled
    number = normalize_phone_number(phone_number)
    aws_sns_client.publish(PhoneNumber=number, Message=sms_message, MessageAttributes={'AWS.SNS.SMS.SenderID': {'DataType': 'String', 'StringValue': sender_id}, 'AWS.SNS.SMS.SMSType': {'DataType': 'String', 'StringValue': 'Transactional'}})


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


def create_apple_wallet_card(vcard_info):
    card_info = Generic()

    membership_year = vcard_info['membership_year']
    membership_expiration = vcard_info['membership_expiration']
    ducati_member_code = vcard_info['ducati_member_code']
    english_full_name = vcard_info['english_full_name']
    hebrew_full_name = vcard_info['hebrew_full_name']
    registration_type = vcard_info['registration_type']
    motorcycle_model = vcard_info['motorcycle_model']

    card_info.add_header_field('year', membership_year, 'year')
    card_info.add_primary_field('code', ducati_member_code, 'ducati_code')
    card_info.add_secondary_field('Name', english_full_name, 'Name')
    card_info.add_secondary_field('local_name', hebrew_full_name, 'שם')
    card_info.add_auxiliary_field('type', registration_type, 'membership_type')
    card_info.add_auxiliary_field('bike', motorcycle_model, 'bike')
    if vcard_info.get('revoked'):
        card_info.add_back_field('note', f'Membership expired - חברות לא בתוקף', '')
    else:
        card_info.add_back_field('note', f'Membership valid until {membership_expiration}', '')

    apple_pass_client = ApplePassGeneratorClient(APPLE_CARD_TEAM_IDENTIFIER, APPLE_CARD_PASS_TYPE_IDENTIFIER, APPLE_CARD_ORGANIZATION_NAME)
    apple_pass = apple_pass_client.get_pass(card_info)
    apple_pass.background_color = 'rgb(204,0,0)'
    apple_pass.logo_text = 'DOC_Israel_Card'
    apple_pass.foreground_color = 'rgb(255,255,255)'
    apple_pass.label_color = 'rgb(255,255,255)'

    resource_dir_path = os.path.join(SCRIPT_DIR, 'resources')
    resources = {
        "logo.png": os.path.join(resource_dir_path, 'assets', 'doc.png'),
        "logo@2x.png": os.path.join(resource_dir_path, 'assets', 'doc@2x.png'),
        "logo@3x.png": os.path.join(resource_dir_path, 'assets', 'doc@3x.png'),
        "icon.png": os.path.join(resource_dir_path, 'assets', 'doc_il.png'),
        "thumbnail.png": os.path.join(resource_dir_path, 'assets', 'doc_il.png'),
        "thumbnail@2x.png": os.path.join(resource_dir_path, 'assets', 'doc_il@2x.png'),
        "thumbnail@3x.png": os.path.join(resource_dir_path, 'assets', 'doc_il@3x.png'),
        "en.lproj/pass.strings": os.path.join(resource_dir_path, 'en.lproj', 'pass.strings'),
        "he.lproj/pass.strings": os.path.join(resource_dir_path, 'he.lproj', 'pass.strings'),
    }

    for resource_name, resource_file_path in resources.items():
        with open(resource_file_path, 'rb') as f:
            apple_pass.add_file(resource_name, f)

    with tempfile.TemporaryDirectory() as temp_dir_path:
        private_key_file_path = os.path.join(temp_dir_path, 'key.pem')

        with open(private_key_file_path, 'w+') as f:
            f.write(APPLE_CARD_PKPASS_PRIVATE_KEY)

        certificate_file_path = os.path.join(resource_dir_path, 'certs', 'certificate.pem')
        wwdr_certificate_file_path = os.path.join(resource_dir_path, 'certs', 'wwdr.pem')
        apple_card = apple_pass.create(certificate_file_path,
                                       private_key_file_path,
                                       wwdr_certificate_file_path,
                                       APPLE_CARD_PKPASS_PRIVATE_KEY_PASSWORD)

        apple_card.seek(0)
        return apple_card.read()


def main():
    logging.basicConfig(level=logging.INFO)
    google_sheets_client.reload()
    total_document_updates_count = 0
    for index, item in enumerate(google_sheets_client.items):
        if total_document_updates_count >= MAX_DOCUMENT_UPDATES:
            logging.info(f'stopping. reached max document updates ({total_document_updates_count})')
            return

        bot_status = ''
        try:
            email_address = item['כתובת אימייל']
            if not email_address:
                logging.debug(f'skipping line {index}, empty phone')
                continue

            email_address = normalize_email_address(email_address)

            phone_number = item['טלפון סלולרי']
            if not phone_number:
                logging.debug(f'skipping line {index}, empty phone')
                continue

            phone_number = normalize_phone_number(phone_number)

            membership_year = item['חברות'].strip()
            ducati_member_code = item['קוד דוקאטי'].strip()
            role = item['תפקיד'].strip()
            hebrew_full_name = item['שם מלא בעברית'].strip()
            english_full_name = item['שם מלא באנגלית'].strip()
            tags = item['אישור'].strip().split(',')
            motorcycle_model = item['דגם אופנוע נוכחי'].strip()
            membership_expiration = item['תפוגה'].strip()
            try:
                membership_expiration = datetime.datetime.strptime(membership_expiration, "%Y-%m-%d")
            except:
                membership_expiration = datetime.datetime(2000, 1, 1)

            last_renewal_reminder_date = item[HEADER_LAST_RENEWAL_REMINDER_DATE].strip()
            try:
                last_renewal_reminder_date = datetime.datetime.strptime(last_renewal_reminder_date, "%Y-%m-%d")
            except:
                last_renewal_reminder_date = datetime.datetime(2000, 1, 1)

            now = datetime.datetime.now()
            registration_type = 'זוגי' if item['זוגי'].lower().strip() == 'y' else 'יחיד'
            revoked = item['עזב'].lower().strip() in ['y', 'rip'] or now > membership_expiration
            vcard_id = f'{email_address}:{phone_number}'
            vcard_id = vcard_id.encode()
            vcard_id = hashlib.sha1(vcard_id).hexdigest()
            short_vcard_id = vcard_id[:10]

            bot_status = item.get(HEADER_BOT_STATUS, '').strip()
            if bot_status == STATUS_UPDATE_TYPO:
                bot_status = STATUS_UPDATE

            if bot_status in [STATUS_ISSUE, STATUS_UPDATE]:
                vcard_info = {
                    "hebrew_full_name": hebrew_full_name,
                    "english_full_name": english_full_name,
                    "membership_year": membership_year,
                    "membership_expiration": membership_expiration.strftime('%Y-%m-%d'),
                    "ducati_member_code": ducati_member_code,
                    "role": role,
                    "tags": tags,
                    "motorcycle_model": motorcycle_model,
                    "registration_type": registration_type,
                }

                if revoked:
                    vcard_info['revoked'] = revoked
                    logging.info(f'revoked card #{index}')

                vcard_info_json = json.dumps(vcard_info)
                vcard_info_json_encoded = vcard_info_json.encode('utf-8')
                aws_s3_resource.meta.client.put_object(Body=vcard_info_json_encoded, Bucket=AWS_S3_BUCKET_NAME, Key=f'card/{vcard_id}.json', ACL='public-read')

                apple_wallet_card = create_apple_wallet_card(vcard_info)
                aws_s3_resource.meta.client.put_object(Body=apple_wallet_card, Bucket=AWS_S3_BUCKET_NAME, Key=f'apple_card/{vcard_id}.pkpass', ACL='public-read')

                short_url_info = json.dumps({
                    'id': vcard_id
                })
                short_url_info = short_url_info.encode('utf-8')
                aws_s3_resource.meta.client.put_object(Body=short_url_info, Bucket=AWS_S3_BUCKET_NAME, Key=f'short/{short_vcard_id}.json', ACL='public-read')

                if not revoked and bot_status != STATUS_UPDATE:
                    _send_issue_notification(ducati_member_code, email_address, hebrew_full_name, phone_number, short_vcard_id)

                new_status = f'{bot_status} - {STATUS_DONE}'
                logging.info(f'issued card for line #{index}')
                google_sheets_client.set_item_field(item, HEADER_BOT_STATUS, new_status)
                time.sleep(RATE_LIMIT_SLEEP_INTERVAL_SECONDS)
                total_document_updates_count += 1

            if not revoked:
                days_till_expiration = (membership_expiration - now).days
                if 0 <= days_till_expiration <= RENEWAL_NOTIFICATIONS_PERIOD_DAYS:
                    days_since_last_renewal_reminder_date = (now - last_renewal_reminder_date).days
                    if days_since_last_renewal_reminder_date >= RENEWAL_NOTIFICATIONS_TTL_DAYS:
                        logging.info(f'renewal notice sent for line #{index}')
                        _send_renewal_notification(ducati_member_code, email_address, hebrew_full_name, phone_number, membership_expiration, days_till_expiration)
                        value = now.strftime("%Y-%m-%d")
                        google_sheets_client.set_item_field(item, HEADER_LAST_RENEWAL_REMINDER_DATE, value)
                        time.sleep(RATE_LIMIT_SLEEP_INTERVAL_SECONDS)
                        total_document_updates_count += 1

        except:
            logging.exception(f'failed issuing card for line #{index}')
            new_status = f'{bot_status} - {STATUS_ERROR}' if bot_status else STATUS_ERROR
            google_sheets_client.set_item_field(item, HEADER_BOT_STATUS, new_status)
            time.sleep(RATE_LIMIT_SLEEP_INTERVAL_SECONDS)
            total_document_updates_count += 1


def _send_issue_notification(ducati_member_code, email_address, hebrew_full_name, phone_number, short_vcard_id):
    is_invalid_ducati_member_code = not re.match(r'^\d+$', ducati_member_code)
    short_card_url = f'https://card.docil.co.il/#/{short_vcard_id}'
    sms_message = SMS_TEMPLATE_ISSUE_SUCCESS.replace('{{hebrew_full_name}}', hebrew_full_name).replace('{{card_url}}', short_card_url)
    if is_invalid_ducati_member_code:
        sms_message = f'{sms_message}\n{TEMPLATE_ISSUE_MISSING_DUCATI_MEMBER_CODE}'
    send_sms(phone_number, sms_message)
    email_message = EMAIL_TEMPLATE_ISSUE_SUCCESS.replace('{{hebrew_full_name}}', hebrew_full_name).replace('{{card_url}}', short_card_url)
    if is_invalid_ducati_member_code:
        email_message = f'{email_message}\n{TEMPLATE_ISSUE_MISSING_DUCATI_MEMBER_CODE}'
    send_email(EMAIL_SUBJECT_ISSUE, email_message, email_address)


def _send_renewal_notification(ducati_member_code, email_address, hebrew_full_name, phone_number, membership_expiration, days_till_expiration):
    is_invalid_ducati_member_code = not re.match(r'^\d+$', ducati_member_code)

    membership_expiration = f'{membership_expiration.strftime("%d/%m/%Y")} (בעוד {days_till_expiration} ימים)'
    if is_invalid_ducati_member_code:
        sms_message = SMS_TEMPLATE_RENEWAL_REQUEST_INVALID_CODE.replace('{{hebrew_full_name}}', hebrew_full_name).replace('{{membership_expiration}}', membership_expiration).replace('{{contact_phone_number}}', CONTACT_PHONE_NUMBER)
    else:
        sms_message = SMS_TEMPLATE_RENEWAL_REQUEST.replace('{{hebrew_full_name}}', hebrew_full_name).replace('{{ducati_member_code}}', ducati_member_code).replace('{{membership_expiration}}', membership_expiration).replace('{{contact_phone_number}}', CONTACT_PHONE_NUMBER)

    send_sms(phone_number, sms_message)

    if is_invalid_ducati_member_code:
        email_message = EMAIL_TEMPLATE_RENEWAL_REQUEST_INVALID_CODE.replace('{{hebrew_full_name}}', hebrew_full_name).replace('{{membership_expiration}}', membership_expiration).replace('{{contact_phone_number}}', CONTACT_PHONE_NUMBER)
    else:
        email_message = EMAIL_TEMPLATE_RENEWAL_REQUEST.replace('{{hebrew_full_name}}', hebrew_full_name).replace('{{ducati_member_code}}', ducati_member_code).replace('{{membership_expiration}}', membership_expiration).replace('{{contact_phone_number}}', CONTACT_PHONE_NUMBER)

    send_email(EMAIL_SUBJECT_RENEWAL_REQUEST, email_message, email_address)


EMAIL_TEMPLATE_ISSUE_SUCCESS = '''
היי {{hebrew_full_name}},
שמחים שהצטרפת למועדון דוקאטי!

הונפק לך כרטיס חבר דיגיטלי בכתובת {{card_url}}

אנא שמור על כתובת זו במועדפים.

אנא הצג כרטיס זה בעת קבלת שירות או טיפול או רכישת אביזרים כדי לקבל את ההטבות המגיעות לחברי המועדון.
שים לב לתוקף הרישום שלך. כחודש לפני תום החברות תקבל התראה לחידוש. במידה ולא תחדש למרות ההתראות, החברות שלך תפוג תוקף אוטומטית.

<img width="180" src="https://card.docil.co.il/preview.png">
'''

SMS_TEMPLATE_ISSUE_SUCCESS = '''
היי {{hebrew_full_name}},
שמחים שהצטרפת למועדון דוקאטי!
הונפק לך כרטיס חבר דיגיטלי
{{card_url}}
'''

TEMPLATE_ISSUE_MISSING_DUCATI_MEMBER_CODE = '''
שים לב: הכרטיס הדיגיטלי שלך איננו מכיל מספר חבר כיוון שלא ביצעת רישום לאתר של דוקאטי העולמית.
אנא גש לאתר המועדון בלינק הבא ובצע את הרישום לאתר של דוקאטי. לאחר מכן כרטיסך יעודכן עם מספר החבר החדש שלך:
https://www.docil.co.il/newreg
'''.strip()

SMS_TEMPLATE_RENEWAL_REQUEST = '''
היי {{hebrew_full_name}},
זו הודעה ממועדון דוקאטי בישראל.

בתאריך {{membership_expiration}} תפוג החברות שלך במועדון.
כדי לא לאבד את הותק שלך ואת ההטבות של המועדון עליך לחדש חברות וזאת לפני שהיא תפוג.

לחידוש חברות במועדון, לחץ על הקישור לטופס בהמשך. יש לשים לב שבטופס צריך לסמן "הייתי כבר חבר" ולהכניס את מספר החבר שלך.

לנוחיותך, מספר החבר שלך במועדון: {{ducati_member_code}}.

טופס הרשמה\חידוש חברות - https://www.docil.co.il/register

---

קיבלת הודעה זו כיוון שאישרת קבלת הודעות אלקטרוניות. 
במידה ואינך מעוניין להמשיך חברותך במועדון ו/או לקבל הודעות נוספות אנא שלח "הסר" בוואטסאפ למספר הבא:
https://wa.me/{{contact_phone_number}}
'''

SMS_TEMPLATE_RENEWAL_REQUEST_INVALID_CODE = '''
היי {{hebrew_full_name}},
זו הודעה ממועדון דוקאטי בישראל.

בתאריך {{membership_expiration}} תפוג החברות שלך במועדון.
כדי לא לאבד את הותק שלך ואת ההטבות של המועדון עליך לחדש חברות וזאת לפני שהיא תפוג.

לחידוש חברות במועדון, לחץ על הקישור לטופס בהמשך. יש לשים לב שבטופס צריך לסמן "הייתי כבר חבר". שמנו לב שעדיין לא סיפקת מספר חבר באתר דוקאטי העולמי ונבקש שבאותה הזדמנות תעשה זאת.

טופס הרשמה\חידוש חברות - https://www.docil.co.il/register

---

קיבלת הודעה זו כיוון שאישרת קבלת הודעות אלקטרוניות. 
במידה ואינך מעוניין להמשיך חברותך במועדון ו/או לקבל הודעות נוספות אנא שלח "הסר" בוואטסאפ למספר הבא:
https://wa.me/{{contact_phone_number}}
'''

EMAIL_TEMPLATE_RENEWAL_REQUEST = '''
היי {{hebrew_full_name}},
זו הודעה ממועדון דוקאטי בישראל.

בתאריך {{membership_expiration}} תפוג החברות שלך במועדון.
כדי לא לאבד את הותק שלך ואת ההטבות של המועדון עליך לחדש חברות וזאת לפני שהיא תפוג.

לחידוש חברות במועדון, לחץ על הקישור לטופס בהמשך. יש לשים לב שבטופס צריך לסמן "הייתי כבר חבר" ולהכניס את מספר החבר שלך.

לנוחיותך, מספר החבר שלך במועדון: {{ducati_member_code}}.

טופס הרשמה\חידוש חברות - https://www.docil.co.il/register

---

קיבלת הודעה זו כיוון שאישרת קבלת הודעות אלקטרוניות. 
במידה ואינך מעוניין להמשיך חברותך במועדון ו/או לקבל הודעות נוספות אנא שלח "הסר" בוואטסאפ למספר הבא:
https://wa.me/{{contact_phone_number}}

<img width="180" src="https://card.docil.co.il/preview.png">

'''

EMAIL_TEMPLATE_RENEWAL_REQUEST_INVALID_CODE = '''
היי {{hebrew_full_name}},
זו הודעה ממועדון דוקאטי בישראל.

בתאריך {{membership_expiration}} תפוג החברות שלך במועדון.
כדי לא לאבד את הותק שלך ואת ההטבות של המועדון עליך לחדש חברות וזאת לפני שהיא תפוג.

לחידוש חברות במועדון, לחץ על הקישור לטופס בהמשך. יש לשים לב שבטופס צריך לסמן "הייתי כבר חבר". שמנו לב שעדיין לא סיפקת מספר חבר באתר דוקאטי העולמי ונבקש שבאותה הזדמנות תעשה זאת.

טופס הרשמה\חידוש חברות - https://www.docil.co.il/register

---

קיבלת הודעה זו כיוון שאישרת קבלת הודעות אלקטרוניות. 
במידה ואינך מעוניין להמשיך חברותך במועדון ו/או לקבל הודעות נוספות אנא שלח "הסר" בוואטסאפ למספר הבא:
https://wa.me/{{contact_phone_number}}

<img width="180" src="https://card.docil.co.il/preview.png">

'''

if __name__ == '__main__':
    main()

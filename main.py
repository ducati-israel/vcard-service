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
from applepassgenerator.models import Barcode, BarcodeFormat, EventTicket
from google.auth import crypt
from google.auth import jwt as google_jwt
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
import requests

dotenv.load_dotenv()

APPLE_CARD_TEAM_IDENTIFIER = "E85N35G3YB"
APPLE_CARD_PASS_TYPE_IDENTIFIER = "pass.com.madappgang.doc.israel"
APPLE_CARD_ORGANIZATION_NAME = "DOC Israel"
APPLE_CARD_PKPASS_PRIVATE_KEY = os.environ['APPLE_CARD_PRIVATE_KEY']
APPLE_CARD_PKPASS_PRIVATE_KEY = APPLE_CARD_PKPASS_PRIVATE_KEY.replace('\\n', '\n')
APPLE_CARD_PKPASS_PRIVATE_KEY_PASSWORD = os.environ['APPLE_CARD_PRIVATE_KEY_PASSWORD']

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))

HEADER_BOT_STATUS = 'סטטוס בוט'
HEADER_CARD_URL = 'קישור לכרטיס'
HEADER_LAST_RENEWAL_REMINDER_DATE = 'תאריך תזכורת חידוש אחרון'
HEADER_LEGACY_MOTORCYCLE_MODEL = 'דגם אופנוע נוכחי'
HEADER_MOTORCYCLE_MODEL_TEMPLATE = 'אופנוע {} - דגם'
HEADER_MOTORCYCLE_LICENSE_TEMPLATE = 'אופנוע {} - מספר רישוי'
HEADER_MOTORCYCLE_YEAR_TEMPLATE = 'אופנוע {} - שנתון'
MAX_MOTORCYCLES = 3
STATUS_UPDATE = 'עדכון'
STATUS_UPDATE_TYPO = 'עידכון'
STATUS_ISSUE = 'הנפקה'
STATUS_DONE = 'בוצע'
STATUS_ERROR = 'שגיאה'

EMAIL_SUBJECT_ISSUE = 'כרטיס החבר שלך במועדון דוקאטי ישראל'
EMAIL_SUBJECT_RENEWAL_REQUEST = 'בקרוב תפוג החברות שלך במועדון דוקאטי ישראל'
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
GOOGLE_WALLET_ISSUER_ID = os.environ['GOOGLE_WALLET_ISSUER_ID']

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


def parse_manufacture_year(value):
    value = str(value or '').strip()
    match = re.match(r'^(\d{4})(\.0+)?$', value)
    return int(match.group(1)) if match else None


def parse_motorcycles(item):
    motorcycles = []
    for motorcycle_index in range(1, MAX_MOTORCYCLES + 1):
        model_header = HEADER_MOTORCYCLE_MODEL_TEMPLATE.format(motorcycle_index)
        if model_header not in item:
            break

        model = str(item.get(model_header) or '').strip()
        license_number = str(item.get(HEADER_MOTORCYCLE_LICENSE_TEMPLATE.format(motorcycle_index)) or '').strip()
        manufacture_year = parse_manufacture_year(item.get(HEADER_MOTORCYCLE_YEAR_TEMPLATE.format(motorcycle_index)))
        if not model and not license_number and not manufacture_year:
            continue

        motorcycles.append({
            "model": model,
            "licenseNumber": license_number,
            "manufactureYear": manufacture_year,
        })

    # backward compatibility - sheets without the per-motorcycle columns
    if not motorcycles:
        legacy_model = str(item.get(HEADER_LEGACY_MOTORCYCLE_MODEL) or '').strip()
        if legacy_model:
            motorcycles.append({
                "model": legacy_model,
                "licenseNumber": '',
                "manufactureYear": None,
            })

    return motorcycles


def format_motorcycle_models(motorcycles):
    return ', '.join(motorcycle['model'] for motorcycle in motorcycles if motorcycle['model'])


def format_motorcycle_summary(motorcycles):
    # wallet card fronts fit a single short line - first model and how many more there are
    models = [motorcycle['model'] for motorcycle in motorcycles if motorcycle['model']]
    if not models:
        return ''

    if len(motorcycles) == 1:
        return models[0]

    return f'{models[0]} +{len(motorcycles) - 1}'


def build_apple_wallet_motorcycle_front_fields(motorcycles):
    # with the qr code on the pass front there is room for two rows of auxiliary fields (up to 4 fields each) -
    # the first row holds the membership type and expiration, the motorcycles get the second row.
    # a single motorcycle gets a field for its model, year and license,
    # otherwise each motorcycle is a single field that carries the year and license as its label
    has_multiple_motorcycles = len(motorcycles) > 1
    fields = []
    for motorcycle_index, motorcycle in enumerate(motorcycles, start=1):
        model_label = f'bike_{motorcycle_index}' if has_multiple_motorcycles else 'bike'
        manufacture_year = str(motorcycle['manufactureYear'] or '')
        if has_multiple_motorcycles:
            details = ' · '.join(detail for detail in [manufacture_year, motorcycle['licenseNumber']] if detail)
            fields.append({'key': f'front_bike_{motorcycle_index}', 'value': motorcycle['model'] or '-', 'label': details or model_label, 'row': 1})
            continue

        fields.append({'key': f'front_bike_{motorcycle_index}', 'value': motorcycle['model'] or '-', 'label': model_label, 'row': 1})
        if manufacture_year:
            fields.append({'key': f'front_bike_{motorcycle_index}_year', 'value': manufacture_year, 'label': 'model_year', 'row': 1})
        if motorcycle['licenseNumber']:
            fields.append({'key': f'front_bike_{motorcycle_index}_license', 'value': motorcycle['licenseNumber'], 'label': 'license_number', 'row': 1})

    return fields


def build_google_wallet_motorcycle_modules(motorcycles):
    modules = []
    for motorcycle_index, motorcycle in enumerate(motorcycles, start=1):

        bike_model = motorcycle['model']
        if not bike_model:
            continue

        modules.append(
            {
                'id': f'bike_{motorcycle_index}_model',
                'localizedHeader': {
                    'defaultValue': {'language': 'he', 'value': f'דגם אופנוע {motorcycle_index}'},
                    'translatedValues': [{'language': 'en-US', 'value': f'Motorcycle Model {motorcycle_index}'}]
                },
                'body': bike_model
            })


        bike_licence_number = motorcycle['licenseNumber']
        if bike_licence_number:
            modules.append(
                {
                    'id': f'bike_{motorcycle_index}_licence_number',
                    'localizedHeader': {
                        'defaultValue': {'language': 'he', 'value': f'מספר רישוי {motorcycle_index}'},
                        'translatedValues': [{'language': 'en-US', 'value': f'License number {motorcycle_index}'}]
                    },
                    'body': bike_licence_number
                })

        bike_manufacture_year = motorcycle['manufactureYear']
        if bike_manufacture_year:
            modules.append(
                {
                    'id': f'bike_{motorcycle_index}_year',
                    'localizedHeader': {
                        'defaultValue': {'language': 'he', 'value': f'שנת ייצור {motorcycle_index}'},
                        'translatedValues': [{'language': 'en-US', 'value': f'Year of manufacture {motorcycle_index}'}]
                    },
                    'body': bike_manufacture_year
                })

    return modules


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


def create_apple_wallet_card(vcard_id, vcard_info):
    card_info = EventTicket()

    membership_year = vcard_info['membership_year']
    membership_expiration = vcard_info['membership_expiration']
    ducati_member_code = vcard_info['ducati_member_code']
    english_full_name = vcard_info['english_full_name']
    hebrew_full_name = vcard_info['hebrew_full_name']
    registration_type = vcard_info['registration_type']
    motorcycles = vcard_info.get('motorcycles', [])
    has_multiple_motorcycles = len(motorcycles) > 1

    card_info.add_header_field('year', membership_year, 'year')
    card_info.add_primary_field('code', ducati_member_code, 'ducati_code')
    card_info.add_secondary_field('Name', english_full_name, 'Name')
    card_info.add_secondary_field('local_name', hebrew_full_name, 'שם')
    card_info.add_auxiliary_field('type', registration_type, 'membership_type')
    card_info.add_auxiliary_field('expiration', membership_expiration, 'expiration')

    for field in build_apple_wallet_motorcycle_front_fields(motorcycles):
        card_info.add_auxiliary_field(field['key'], field['value'], field['label'])
        # the library has no row argument - fields are serialized from their attributes
        card_info.auxiliary_fields[-1].row = field['row']

    if vcard_info.get('revoked'):
        card_info.add_back_field('note', f'Membership expired - חברות לא בתוקף', '')
    else:
        card_info.add_back_field('note', f'Membership valid until {membership_expiration}', '')

    # the back of the pass has no space limits - every motorcycle is listed there in full
    for motorcycle_index, motorcycle in enumerate(motorcycles, start=1):
        model_label = f'bike_{motorcycle_index}' if has_multiple_motorcycles else 'bike'
        card_info.add_back_field(f'bike_{motorcycle_index}_model', motorcycle['model'] or '-', model_label)
        if motorcycle['licenseNumber']:
            card_info.add_back_field(f'bike_{motorcycle_index}_license', motorcycle['licenseNumber'], 'license_number')
        if motorcycle['manufactureYear']:
            card_info.add_back_field(f'bike_{motorcycle_index}_year', str(motorcycle['manufactureYear']), 'manufacture_year')

    card_info.add_back_field('benefits', 'https://www.docil.co.il/?page_id=479', 'benefits')

    apple_pass_client = ApplePassGeneratorClient(APPLE_CARD_TEAM_IDENTIFIER, APPLE_CARD_PASS_TYPE_IDENTIFIER, APPLE_CARD_ORGANIZATION_NAME)
    apple_pass = apple_pass_client.get_pass(card_info)
    apple_pass.background_color = 'rgb(204,0,0)'
    apple_pass.logo_text = 'DOC_Israel_Card'
    apple_pass.foreground_color = 'rgb(255,255,255)'
    apple_pass.label_color = 'rgb(255,255,255)'
    # same qr code as the google wallet card
    apple_pass.barcode = Barcode(vcard_id, BarcodeFormat.QR)
    # same end of validity as the google wallet card - the pass shows as expired after it
    apple_pass.expiration_date = f'{membership_expiration}T23:59:59+02:00'

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


def create_and_update_google_wallet_card(vcard_id, vcard_info):
    issuer_id = GOOGLE_WALLET_ISSUER_ID
    class_suffix = "docil_membership_v8"

    membership_expiration = vcard_info['membership_expiration']
    ducati_member_code = vcard_info['ducati_member_code']
    english_full_name = vcard_info['english_full_name']
    hebrew_full_name = vcard_info['hebrew_full_name']
    registration_type = vcard_info['registration_type']
    motorcycles = vcard_info.get('motorcycles', [])

    # Build pass class definition
    class_id = f"{issuer_id}.{class_suffix}"
    pass_class = {
        'id': class_id,
        'classTemplateInfo': {
            'cardTemplateOverride': {
                'cardRowTemplateInfos': [
                    {'oneItem': {'item': {'firstValue': {'fields': [{'fieldPath': "object.textModulesData['hebrew_name']"}]}}}},
                    {'oneItem': {'item': {'firstValue': {'fields': [{'fieldPath': "object.textModulesData['membership_number']"}]}}}},
                    {'twoItems': {
                        'startItem': {'firstValue': {'fields': [{'fieldPath': "object.textModulesData['membership_type']"}]}},
                        'endItem': {'firstValue': {'fields': [{'fieldPath': "object.textModulesData['expiry_date']"}]}}
                    }}
                ]
            }
        }
    }

    # Build pass object definition
    object_suffix = f"member_{ducati_member_code.replace('-', '_')}_v8"
    object_id = f"{issuer_id}.{object_suffix}"

    google_wallet_fields = [
            {'id': 'hebrew_name', 'localizedHeader': {'defaultValue': {'language': 'he', 'value': 'שם'}, 'translatedValues': [{'language': 'en-US', 'value': 'Name'}]}, 'body': hebrew_full_name},
            {'id': 'membership_number', 'localizedHeader': {'defaultValue': {'language': 'he', 'value': 'קוד דוקאטי'}, 'translatedValues': [{'language': 'en-US', 'value': 'Ducati Code'}]}, 'body': ducati_member_code},
            {'id': 'membership_type', 'localizedHeader': {'defaultValue': {'language': 'he', 'value': 'סוג חברות'}, 'translatedValues': [{'language': 'en-US', 'value': 'Member Type'}]}, 'body': registration_type},
            {'id': 'expiry_date', 'localizedHeader': {'defaultValue': {'language': 'he', 'value': 'בתוקף עד'}, 'translatedValues': [{'language': 'en-US', 'value': 'Valid Until'}]}, 'body': membership_expiration}
        ]

    google_wallet_fields.extend(build_google_wallet_motorcycle_modules(motorcycles))

    pass_object = {
        'id': object_id,
        'classId': class_id,
        'genericType': 'GENERIC_TYPE_UNSPECIFIED',
        'state': 'ACTIVE',
        'cardTitle': {
            'defaultValue': {'language': 'he', 'value': 'כרטיס DOC ישראל'},
            'translatedValues': [{'language': 'en-US', 'value': 'DOC Israel Card'}]
        },
        'header': {
            'defaultValue': {'language': 'he', 'value': hebrew_full_name},
            'translatedValues': [{'language': 'en-US', 'value': english_full_name}]
        },
        'subheader': {
            'defaultValue': {'language': 'he', 'value': f"בתוקף עד {membership_expiration}"},
            'translatedValues': [{'language': 'en-US', 'value': f"Valid Until {membership_expiration}"}]
        },
        'hexBackgroundColor': '#CB1517',
        'logo': {
            'sourceUri': {'uri': 'https://ducati-israel-vcard.s3.amazonaws.com/images/logo.png'},
            'contentDescription': {'defaultValue': {'language': 'en-US', 'value': 'DOC Israel Logo'}}
        },
        'heroImage': {
            'sourceUri': {'uri': 'https://ducati-israel-vcard.s3.amazonaws.com/images/hero.png'},
            'contentDescription': {'defaultValue': {'language': 'en-US', 'value': 'DOC Israel Banner'}}
        },
        'textModulesData': google_wallet_fields,
        'barcode': {
            'type': 'QR_CODE',
            'value': vcard_id,
        },
        'validTimeInterval': {
            'start': {
                'date': f'{membership_expiration.split('-')[0]}-01-01T00:00:00+02:00',
            },
            'end': {
                'date': f'{membership_expiration}T23:59:59+02:00',
            },
        },
        'linksModuleData': {
            'uris': [
                {
                    'uri': 'https://www.docil.co.il/?page_id=479',
                    'description': 'הטבות לחברי מועדון דוקאטי',
                },
            ],
        },
        'notifications': {
            'expiryNotification': {
                'enableNotification': True,
            },
        },
    }

    # Get authenticated credentials
    credentials = Credentials.from_service_account_info(
        google_service_account_credentials,
        scopes=['https://www.googleapis.com/auth/wallet_object.issuer']
    )

    # 1. Create JWT for "Add to Wallet" URL
    claims = {
        'iss': credentials.service_account_email,
        'aud': 'google',
        'typ': 'savetowallet',
        'origins': [],
        'payload': {
            'genericClasses': [pass_class],
            'genericObjects': [pass_object]
        }
    }
    signer = crypt.RSASigner.from_service_account_info(google_service_account_credentials)
    token = google_jwt.encode(signer, claims).decode('utf-8')
    jwt_url = f"https://pay.google.com/gp/v/save/{token}"

    # 2. Try to update existing card via API (for users who already saved it)
    credentials.refresh(Request())
    api_url = f"https://walletobjects.googleapis.com/walletobjects/v1/genericObject/{object_id}"
    headers = {
        'Authorization': f'Bearer {credentials.token}',
        'Content-Type': 'application/json'
    }

    try:
        response = requests.patch(api_url, headers=headers, json=pass_object)
        if response.status_code == 200:
            logging.info(f'Google Wallet object updated: {object_id}')
        elif response.status_code == 404:
            logging.debug(f'Google Wallet object not found (not yet saved by user): {object_id}')
        else:
            logging.warning(f'Google Wallet update failed ({response.status_code}): {response.text}')
    except Exception as e:
        logging.warning(f'Google Wallet update error: {e}')

    return {'url': jwt_url}


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

            hebrew_full_name = item['שם מלא בעברית'].strip()
            if not hebrew_full_name:
                logging.debug(f'skipping line {index}, empty hebrew full name')
                continue

            english_full_name = item['שם מלא באנגלית'].strip()
            if not english_full_name:
                logging.debug(f'skipping line {index}, empty english full name')
                continue

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
            tags = item['אישור'].strip().split(',')
            motorcycles = parse_motorcycles(item)
            motorcycle_model = format_motorcycle_models(motorcycles)
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
                    "motorcycles": motorcycles,
                    "registration_type": registration_type,
                }

                if revoked:
                    vcard_info['revoked'] = revoked
                    logging.info(f'revoked card #{index}')

                vcard_info_json = json.dumps(vcard_info)
                vcard_info_json_encoded = vcard_info_json.encode('utf-8')
                aws_s3_resource.meta.client.put_object(Body=vcard_info_json_encoded, Bucket=AWS_S3_BUCKET_NAME, Key=f'card/{vcard_id}.json', ACL='public-read')

                apple_wallet_card = create_apple_wallet_card(vcard_id, vcard_info)
                aws_s3_resource.meta.client.put_object(Body=apple_wallet_card, Bucket=AWS_S3_BUCKET_NAME, Key=f'apple_card/{vcard_id}.pkpass', ACL='public-read')

                google_wallet_card = create_and_update_google_wallet_card(vcard_id, vcard_info)
                google_wallet_card_json = json.dumps(google_wallet_card)
                google_wallet_card_json_encoded = google_wallet_card_json.encode('utf-8')
                aws_s3_resource.meta.client.put_object(Body=google_wallet_card_json_encoded, Bucket=AWS_S3_BUCKET_NAME, Key=f'android_card/{vcard_id}.json', ACL='public-read', ContentType='application/json')

                short_url_info = json.dumps({
                    'id': vcard_id
                })
                short_url_info = short_url_info.encode('utf-8')
                aws_s3_resource.meta.client.put_object(Body=short_url_info, Bucket=AWS_S3_BUCKET_NAME, Key=f'short/{short_vcard_id}.json', ACL='public-read')

                if not revoked and bot_status != STATUS_UPDATE:
                    _send_issue_notification(ducati_member_code, email_address, hebrew_full_name, phone_number, short_vcard_id, membership_expiration)

                new_status = f'{bot_status} - {STATUS_DONE}'
                logging.info(f'issued card for line #{index}')
                google_sheets_client.set_item_field(item, HEADER_BOT_STATUS, new_status)

                short_card_url = f'https://card.docil.co.il/#/{short_vcard_id}'
                if item[HEADER_CARD_URL] != short_card_url:
                    google_sheets_client.set_item_field(item, HEADER_CARD_URL, short_card_url)

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


def _send_issue_notification(ducati_member_code, email_address, hebrew_full_name, phone_number, short_vcard_id, membership_expiration):
    is_invalid_ducati_member_code = not re.match(r'^\d+$', ducati_member_code)
    membership_expiration = membership_expiration.strftime("%d/%m/%Y")
    short_card_url = f'https://card.docil.co.il/#/{short_vcard_id}'
    sms_message = SMS_TEMPLATE_ISSUE_SUCCESS.replace('{{hebrew_full_name}}', hebrew_full_name).replace('{{card_url}}', short_card_url)
    if is_invalid_ducati_member_code:
        sms_message = f'{sms_message}\n{TEMPLATE_ISSUE_MISSING_DUCATI_MEMBER_CODE}'
    send_sms(phone_number, sms_message)
    email_message = EMAIL_TEMPLATE_ISSUE_SUCCESS.replace('{{hebrew_full_name}}', hebrew_full_name).replace('{{membership_expiration}}', membership_expiration).replace('{{ducati_member_code}}', ducati_member_code).replace('{{card_url}}', short_card_url)
    if is_invalid_ducati_member_code:
        email_message = f'{email_message}\n{TEMPLATE_ISSUE_MISSING_DUCATI_MEMBER_CODE}'
    send_email(EMAIL_SUBJECT_ISSUE, email_message, email_address)


def _send_renewal_notification(ducati_member_code, email_address, hebrew_full_name, phone_number, membership_expiration, days_till_expiration):
    is_invalid_ducati_member_code = not re.match(r'^\d+$', ducati_member_code)

    membership_expiration = f'{membership_expiration.strftime("%d/%m/%Y")} (בעוד {days_till_expiration} ימים)'
    if is_invalid_ducati_member_code:
        sms_message = SMS_TEMPLATE_RENEWAL_REQUEST_INVALID_CODE.replace('{{hebrew_full_name}}', hebrew_full_name).replace('{{hebrew_full_name}}', hebrew_full_name).replace('{{membership_expiration}}', membership_expiration).replace('{{contact_phone_number}}', CONTACT_PHONE_NUMBER)
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
אנחנו שמחים שהצטרפת למועדון דוקאטי! 

מספר החבר שלך הוא {{ducati_member_code}}
בנוסף, הונפק לך כרטיס חבר דיגיטלי בכתובת {{card_url}}

אנא שמור על כתובת זו במועדפים.

אנא הצג כרטיס זה בעת קבלת שירות או טיפול או רכישת אביזרים כדי לקבל את ההטבות המגיעות לחברי המועדון.

תאריך התפוגה של החברות שלך במועדון: {{membership_expiration}}
כחודש לפני תום החברות תקבל התראה לחידוש. במידה ולא תחדש למרות ההתראות, החברות שלך תפוג תוקף אוטומטית.

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

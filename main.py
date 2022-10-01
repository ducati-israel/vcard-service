import hashlib
import logging
import os

import boto3
from google.oauth2 import service_account
from googleapiclient.discovery import build
import json
import dotenv
import phonenumbers
import xlsxwriter.utility

dotenv.load_dotenv()

GOOGLE_SPREADSHEET_ID = os.environ['GOOGLE_SPREADSHEET_ID']
GOOGLE_SERVICE_ACCOUNT_CREDENTIALS = os.environ['GOOGLE_SERVICE_ACCOUNT_CREDENTIALS']

AWS_S3_BUCKET_NAME = os.environ['AWS_S3_BUCKET_NAME']
AWS_ACCESS_KEY_ID = os.environ['AWS_ACCESS_KEY_ID']
AWS_SECRET_ACCESS_KEY = os.environ['AWS_SECRET_ACCESS_KEY']
AWS_REGION = os.environ['AWS_REGION']

aws_session = boto3.Session(aws_access_key_id=AWS_ACCESS_KEY_ID, aws_secret_access_key=AWS_SECRET_ACCESS_KEY, region_name=AWS_REGION)
s3_resource = aws_session.resource('s3')

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))

HEADER_BOT_STATUS = 'סטטוס בוט'
STATUS_ISSUE = 'הנפקה'
STATUS_ISSUE_DONE = 'הנפקה - בוצע'
STATUS_INPUT_ERROR = 'שגיאה'
STATUS_INPUT_ERROR_DONE = 'שגיאה - בוצע'


class GoogleSheetsClient:
    def __init__(self, google_service_account_credentials, spreadsheet_id=GOOGLE_SPREADSHEET_ID, spreadsheet_name='Sheet1'):
        google_service_account_credentials = json.loads(bytes.fromhex(google_service_account_credentials).decode())
        google_service_account_credentials = service_account.Credentials.from_service_account_info(google_service_account_credentials)
        self.google_sheets_resource = build('sheets', 'v4', credentials=google_service_account_credentials)

        self.spreadsheet_name = spreadsheet_name
        self.spreadsheet_id = spreadsheet_id
        self.rows = []
        self.headers = []
        self.items = []

    def __enter__(self):
        self._load()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def _load(self, spreadsheet_range='A:ZZ'):
        result = self.google_sheets_resource.spreadsheets().values().get(spreadsheetId=self.spreadsheet_id, range=f'{self.spreadsheet_name}!{spreadsheet_range}').execute()
        rows = result.get('values', [])

        if not rows:
            logging.warning(f'no records for spreadsheet="{self.spreadsheet_id}"')
            return

        self.rows = rows
        rows = iter(rows)
        headers = next(rows)
        items = []
        for row_index, row in enumerate(rows):
            item = {}
            for header_index, header in enumerate(headers):
                item[header] = row[header_index] if len(row) > header_index else ''

            item['id'] = row_index + 1  # we skipped the headers
            items.append(item)

        self.headers = headers
        self.items = items

    def set_item_field(self, item, field_name, value):
        row = item['id'] + 1
        if field_name not in self.headers:
            self.add_header(field_name)

        column_index = self.headers.index(field_name)
        range_start_letter = xlsxwriter.utility.xl_col_to_name(column_index)
        range_end_letter = xlsxwriter.utility.xl_col_to_name(column_index + 1)
        spreadsheet_range = f'{self.spreadsheet_name}!{range_start_letter}{row}:{range_end_letter}{row}'
        self._update_cell(spreadsheet_range, value)
        self._load()

    def add_header(self, field_name):
        new_header_index = len(self.headers)
        range_start_letter = xlsxwriter.utility.xl_col_to_name(new_header_index)
        range_end_letter = xlsxwriter.utility.xl_col_to_name(new_header_index + 1)
        spreadsheet_range = f'{self.spreadsheet_name}!{range_start_letter}:{range_end_letter}'
        self._update_cell(spreadsheet_range, field_name)
        self._load()

    def _update_cell(self, spreadsheet_range, value):
        self.google_sheets_resource.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=spreadsheet_range,
            body={
                "majorDimension": "ROWS",
                "values": [[value]]
            },
            valueInputOption="USER_ENTERED"
        ).execute()


def normalize_phone_number(phone_number, default_country_code="IL"):
    try:
        phone_number = phonenumbers.parse(phone_number, default_country_code)
        return f'+{phone_number.country_code}{phone_number.national_number}'
    except phonenumbers.NumberParseException:
        logging.exception(f'failed normalizing phone number "{phone_number}"')
        return ''


def normalize_email_address(email_address):
    return email_address.lower().strip()


def main():
    logging.basicConfig(level=logging.INFO)
    with GoogleSheetsClient(GOOGLE_SERVICE_ACCOUNT_CREDENTIALS) as google_sheets_client:
        for index, item in enumerate(google_sheets_client.items):
            membership_year = item['חברות']
            ducati_member_code = item['קוד דוקאטי']
            role = item['תפקיד']
            hebrew_full_name = item['שם מלא בעברית']
            english_full_name = item['שם מלא באנגלית']
            email_address = item['כתובת אימייל']
            email_address = normalize_email_address(email_address)
            phone_number = item['מספר טלפון סלולרי']
            phone_number = normalize_phone_number(phone_number)
            motorcycle_model = item['דגם אופנוע נוכחי']
            registration_type = item['רישום יחיד או בזוג']
            vcard_id = f'{email_address}:{phone_number}'
            vcard_id = vcard_id.encode()
            vcard_id = hashlib.sha1(vcard_id).hexdigest()
            short_vcard_id = vcard_id[:10]

            bot_status = item.get(HEADER_BOT_STATUS, '')
            if bot_status == STATUS_ISSUE:
                logging.info(f'issuing card for member #{index}')

                vcard = {
                    "hebrew_full_name": hebrew_full_name,
                    "english_full_name": english_full_name,
                    "membership_year": membership_year,
                    "ducati_member_code": ducati_member_code,
                    "role": role,
                    "motorcycle_model": motorcycle_model,
                    "registration_type": registration_type,
                }
                vcard = json.dumps(vcard)
                vcard = vcard.encode('utf-8')
                s3_resource.meta.client.put_object(Body=vcard, Bucket=AWS_S3_BUCKET_NAME, Key=f'card/{vcard_id}.json', ACL='public-read')

                short_url_info = json.dumps({
                    'id': vcard_id
                })
                short_url_info = short_url_info.encode('utf-8')
                s3_resource.meta.client.put_object(Body=short_url_info, Bucket=AWS_S3_BUCKET_NAME, Key=f'short/{short_vcard_id}.json', ACL='public-read')

                google_sheets_client.set_item_field(item, HEADER_BOT_STATUS, STATUS_ISSUE_DONE)
                # TODO send sms message
                # TODO send email message

            if bot_status == STATUS_INPUT_ERROR:
                logging.info('notifying user of input error')
                google_sheets_client.set_item_field(item, HEADER_BOT_STATUS, STATUS_INPUT_ERROR_DONE)
                # TODO send sms message
                # TODO send email message


if __name__ == '__main__':
    main()

import os
import tempfile
import dotenv
from applepassgenerator.client import ApplePassGeneratorClient
from applepassgenerator.models import *


dotenv.load_dotenv()
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

TEAM_IDENTIFIER = "E85N35G3YB"
PASS_TYPE_IDENTIFIER = "pass.com.madappgang.doc.israel"
ORGANIZATION_NAME = "DOC Israel"
CERTIFICATE_PATH = "certs/certificate.pem"
WWDR_CERTIFICATE_PATH = "certs/wwdr.pem"

PKPASS_PRIVATE_KEY = os.environ['PKPASS_PRIVATE_KEY']
PKPASS_CERTIFICATE_PASSWORD = os.environ['PKPASS_CERTIFICATE_PASSWORD']


def local_file_name(f_name):
    return f'{SCRIPT_DIR}/{f_name}'


def get_apple_pass_bytes(vcard_info):
    # All the strings in this function are pulled from locale folders, in code we use alias strings
    card_info = Generic()

    card_info.add_header_field('year', vcard_info['membership_year'], 'year')
    card_info.add_primary_field('code', vcard_info['ducati_member_code'], 'ducati_code')
    card_info.add_secondary_field('Name', vcard_info['english_full_name'], 'Name')
    card_info.add_secondary_field('local_name', vcard_info['hebrew_full_name'], 'שם')
    card_info.add_auxiliary_field('type', vcard_info['registration_type'], 'membership_type')
    card_info.add_auxiliary_field('bike', vcard_info['motorcycle_model'], 'bike')
    card_info.add_back_field('note', f'Membership period until the end of October {vcard_info["membership_year"]}', '')

    applepassgenerator_client = ApplePassGeneratorClient(TEAM_IDENTIFIER, PASS_TYPE_IDENTIFIER, ORGANIZATION_NAME)
    apple_pass = applepassgenerator_client.get_pass(card_info)
    apple_pass.background_color = 'rgb(204,0,0)'
    apple_pass.logo_text = 'DOC_israel_card'
    apple_pass.foreground_color = 'rgb(255,255,255)'
    apple_pass.label_color = 'rgb(255,255,255)'
    # Add logo/icon/strip image to file
    apple_pass.add_file("logo.png", open(local_file_name("assets/doc.png"), "rb"))
    apple_pass.add_file("logo@2x.png", open(local_file_name("assets/doc@2x.png"), "rb"))
    apple_pass.add_file("logo@3x.png", open(local_file_name("assets/doc@3x.png"), "rb"))
    apple_pass.add_file("icon.png", open(local_file_name("assets/doc_il.png"), "rb"))
    apple_pass.add_file("thumbnail.png", open(local_file_name("assets/doc_il.png"), "rb"))
    apple_pass.add_file("thumbnail@2x.png", open(local_file_name("assets/doc_il@2x.png"), "rb"))
    apple_pass.add_file("thumbnail@3x.png", open(local_file_name("assets/doc_il@3x.png"), "rb"))
    # locale files
    apple_pass.add_file("en.lproj/pass.strings", open(local_file_name("en.lproj/pass.strings"), "rb"))
    apple_pass.add_file("he.lproj/pass.strings", open(local_file_name("he.lproj/pass.strings"), "rb"))

    private_key_file_name = ''
    with tempfile.NamedTemporaryFile(delete=False) as temp:
        key_content = PKPASS_PRIVATE_KEY.replace('\\n', '\n')
        temp.write(key_content.encode())
        private_key_file_name = temp.name

    pass_bytes = apple_pass.create(local_file_name(CERTIFICATE_PATH),
                                   private_key_file_name,
                                   local_file_name(WWDR_CERTIFICATE_PATH),
                                   PKPASS_CERTIFICATE_PASSWORD)

    os.unlink(private_key_file_name)

    return pass_bytes

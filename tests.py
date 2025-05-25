import unittest
import os
import json
from unittest.mock import patch, MagicMock, mock_open
from googleapiclient.errors import HttpError

# Modules to be tested
from google_wallet_setup import create_google_wallet_class
from main import create_google_wallet_object, normalize_phone_number, normalize_email_address

# Dummy service account JSON string
DUMMY_SERVICE_ACCOUNT_JSON = '{"client_email": "test@example.com", "private_key": "dummy_key"}'
DUMMY_ISSUER_ID = "1234567890123456789"
DUMMY_CLASS_SUFFIX = "docIsraelMembershipCardV2" # Matches the one in google_wallet_setup
DUMMY_CLASS_ID = f"{DUMMY_ISSUER_ID}.{DUMMY_CLASS_SUFFIX}"
DUMMY_OBJECT_SUFFIX = "test_object_id" # Used for vcard_id in object tests
DUMMY_WALLET_OBJECT_ID = f"{DUMMY_ISSUER_ID}.{DUMMY_OBJECT_SUFFIX}"


class TestMainNormalization(unittest.TestCase): # Renamed for clarity

    def test_normalize_phone_number(self):
        self.assertEqual('+972505600011', normalize_phone_number('0505600011'))
        self.assertEqual('+972505600011', normalize_phone_number(' 0505600011 '))
        self.assertEqual('+972505600011', normalize_phone_number('\t0505600011\t'))
        # self.assertEqual('+972505600011', normalize_phone_number('0505600011')) # Duplicate
        self.assertEqual('+972505600011', normalize_phone_number('050-5600011'))
        self.assertEqual('+972505600011', normalize_phone_number('050 5600011'))

    def test_normalize_phone_number_explicit_country_code(self):
        self.assertEqual('+1505600011', normalize_phone_number('+1505600011'))
        self.assertEqual('+1505600011', normalize_phone_number('+(1)505600011'))

    def test_normalize_invalid_phone_number(self):
        self.assertEqual('', normalize_phone_number(''))

    def test_normalize_email_address(self):
        self.assertEqual('example@gmail.com', normalize_email_address('example@gmail.com'))
        self.assertEqual('example@gmail.com', normalize_email_address('Example@gmail.com'))
        self.assertEqual('example@gmail.com', normalize_email_address(' ExampLe@gmail.Com'))
        self.assertEqual('example@gmail.com', normalize_email_address('\tExampLe@gmail.Com\t'))


class TestCreateGoogleWalletClass(unittest.TestCase):

    @patch.dict(os.environ, {
        'GOOGLE_WALLET_SERVICE_ACCOUNT_CREDENTIALS': DUMMY_SERVICE_ACCOUNT_JSON,
        'GOOGLE_WALLET_ISSUER_ID': DUMMY_ISSUER_ID
    })
    @patch('google_wallet_setup.build')
    def test_create_new_class_success(self, mock_build):
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        
        # Simulate class does not exist (GET raises 404)
        mock_service.genericclass().get().execute.side_effect = HttpError(
            resp=MagicMock(status=404), content=b'Class not found'
        )
        # Simulate successful insert
        mock_service.genericclass().insert().execute.return_value = {'id': DUMMY_CLASS_ID, 'message': 'Class created'}

        with patch('builtins.print') as mock_print: # To capture print statements
            create_google_wallet_class()

        mock_build.assert_called_once_with('walletobjects', 'v1', credentials=unittest.mock.ANY)
        mock_service.genericclass().get.assert_called_once_with(resourceId=DUMMY_CLASS_ID)
        mock_service.genericclass().insert.assert_called_once()
        
        # Check payload basics
        insert_call_args = mock_service.genericclass().insert.call_args[1]['body']
        self.assertEqual(insert_call_args['id'], DUMMY_CLASS_ID)
        self.assertEqual(insert_call_args['issuerName'], "DOC Israel")
        self.assertIn('classTemplateInfo', insert_call_args)
        self.assertEqual(insert_call_args['reviewStatus'], "UNDER_REVIEW")
        mock_print.assert_any_call(f"Class {DUMMY_CLASS_ID} does not exist. Attempting to insert.")

    @patch.dict(os.environ, {
        'GOOGLE_WALLET_SERVICE_ACCOUNT_CREDENTIALS': DUMMY_SERVICE_ACCOUNT_JSON,
        'GOOGLE_WALLET_ISSUER_ID': DUMMY_ISSUER_ID
    })
    @patch('google_wallet_setup.build')
    def test_update_existing_class_success(self, mock_build):
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        # Simulate class exists
        mock_service.genericclass().get().execute.return_value = {'id': DUMMY_CLASS_ID, 'some_data': 'old_data'}
        # Simulate successful update
        mock_service.genericclass().update().execute.return_value = {'id': DUMMY_CLASS_ID, 'message': 'Class updated'}

        with patch('builtins.print') as mock_print:
            create_google_wallet_class()

        mock_build.assert_called_once_with('walletobjects', 'v1', credentials=unittest.mock.ANY)
        mock_service.genericclass().get.assert_called_once_with(resourceId=DUMMY_CLASS_ID)
        mock_service.genericclass().update.assert_called_once()
        mock_service.genericclass().insert.assert_not_called()

        update_call_args = mock_service.genericclass().update.call_args[1]['body']
        self.assertEqual(update_call_args['id'], DUMMY_CLASS_ID)
        mock_print.assert_any_call(f"Class {DUMMY_CLASS_ID} already exists. Attempting to update.")

    @patch.dict(os.environ, {'GOOGLE_WALLET_ISSUER_ID': DUMMY_ISSUER_ID}) # Credentials missing
    @patch('google_wallet_setup.build') # To prevent actual API call attempt
    def test_missing_env_var_credentials(self, mock_build):
        with patch('builtins.print') as mock_print:
            create_google_wallet_class()
        mock_print.assert_any_call("Error: GOOGLE_WALLET_SERVICE_ACCOUNT_CREDENTIALS environment variable not set.")
        mock_build.assert_not_called()

    @patch.dict(os.environ, {'GOOGLE_WALLET_SERVICE_ACCOUNT_CREDENTIALS': DUMMY_SERVICE_ACCOUNT_JSON}) # Issuer ID missing
    @patch('google_wallet_setup.build')
    def test_missing_env_var_issuer_id(self, mock_build):
        with patch('builtins.print') as mock_print:
            create_google_wallet_class()
        mock_print.assert_any_call("Error: GOOGLE_WALLET_ISSUER_ID environment variable not set.")
        mock_build.assert_not_called()

    @patch.dict(os.environ, {
        'GOOGLE_WALLET_SERVICE_ACCOUNT_CREDENTIALS': 'this is not json',
        'GOOGLE_WALLET_ISSUER_ID': DUMMY_ISSUER_ID
    })
    @patch('google_wallet_setup.build')
    def test_invalid_credentials_json(self, mock_build):
        with patch('builtins.print') as mock_print:
            create_google_wallet_class()
        mock_print.assert_any_call("Error: GOOGLE_WALLET_SERVICE_ACCOUNT_CREDENTIALS is not valid JSON.")
        mock_build.assert_not_called()

    @patch.dict(os.environ, {
        'GOOGLE_WALLET_SERVICE_ACCOUNT_CREDENTIALS': DUMMY_SERVICE_ACCOUNT_JSON,
        'GOOGLE_WALLET_ISSUER_ID': DUMMY_ISSUER_ID
    })
    @patch('google_wallet_setup.build')
    def test_api_error_on_get_other_than_404(self, mock_build):
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.genericclass().get().execute.side_effect = HttpError(
            resp=MagicMock(status=500), content=b'Internal server error'
        )
        with patch('builtins.print') as mock_print:
            create_google_wallet_class()
        mock_print.assert_any_call("An error occurred: <HttpError 500 when requesting None returned \"Internal server error\">")


class TestCreateGoogleWalletObject(unittest.TestCase):
    sample_vcard_info = {
        'english_full_name': 'John Doe',
        'ducati_member_code': 'D1234',
        'membership_expiration': '2025-12-31',
        'motorcycle_model': 'Panigale V4',
        'membership_year': '2024',
        'revoked': False
    }
    sample_vcard_id = DUMMY_OBJECT_SUFFIX # Using the same suffix for consistency

    @patch.dict(os.environ, {
        'GOOGLE_WALLET_SERVICE_ACCOUNT_CREDENTIALS': DUMMY_SERVICE_ACCOUNT_JSON,
        'GOOGLE_WALLET_ISSUER_ID': DUMMY_ISSUER_ID,
        'GOOGLE_WALLET_CLASS_ID': DUMMY_CLASS_ID
    })
    @patch('main.build') # Patching 'main.build' as create_google_wallet_object is in main.py
    def test_create_new_object_success(self, mock_build):
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_insert_response = {'saveUri': 'mock_save_uri_new', 'id': DUMMY_WALLET_OBJECT_ID}
        mock_service.genericobject().insert().execute.return_value = mock_insert_response

        result_uri = create_google_wallet_object(self.sample_vcard_info, self.sample_vcard_id)

        self.assertEqual(result_uri, 'mock_save_uri_new')
        mock_service.genericobject().insert.assert_called_once()
        
        payload = mock_service.genericobject().insert.call_args[1]['body']
        self.assertEqual(payload['id'], DUMMY_WALLET_OBJECT_ID)
        self.assertEqual(payload['classId'], DUMMY_CLASS_ID)
        self.assertEqual(payload['state'], 'ACTIVE')
        self.assertTrue(any(item['id'] == 'english_full_name' and item['body'] == 'John Doe' for item in payload['textModulesData']))

    @patch.dict(os.environ, {
        'GOOGLE_WALLET_SERVICE_ACCOUNT_CREDENTIALS': DUMMY_SERVICE_ACCOUNT_JSON,
        'GOOGLE_WALLET_ISSUER_ID': DUMMY_ISSUER_ID,
        'GOOGLE_WALLET_CLASS_ID': DUMMY_CLASS_ID
    })
    @patch('main.build')
    def test_create_object_revoked(self, mock_build):
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        revoked_vcard_info = {**self.sample_vcard_info, 'revoked': True}
        mock_service.genericobject().insert().execute.return_value = {'saveUri': 'mock_save_uri_revoked'}

        create_google_wallet_object(revoked_vcard_info, self.sample_vcard_id)
        
        payload = mock_service.genericobject().insert.call_args[1]['body']
        self.assertEqual(payload['state'], 'EXPIRED')

    @patch.dict(os.environ, {
        'GOOGLE_WALLET_SERVICE_ACCOUNT_CREDENTIALS': DUMMY_SERVICE_ACCOUNT_JSON,
        'GOOGLE_WALLET_ISSUER_ID': DUMMY_ISSUER_ID,
        'GOOGLE_WALLET_CLASS_ID': DUMMY_CLASS_ID
    })
    @patch('main.build')
    def test_object_already_exists_returns_uri(self, mock_build):
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        
        # Simulate insert fails due to conflict
        mock_service.genericobject().insert().execute.side_effect = HttpError(
            resp=MagicMock(status=409), content=b'Object already exists'
        )
        # Simulate successful get
        mock_get_response = {'saveUri': 'existing_mock_save_uri', 'id': DUMMY_WALLET_OBJECT_ID}
        mock_service.genericobject().get().execute.return_value = mock_get_response

        result_uri = create_google_wallet_object(self.sample_vcard_info, self.sample_vcard_id)

        self.assertEqual(result_uri, 'existing_mock_save_uri')
        mock_service.genericobject().insert.assert_called_once()
        mock_service.genericobject().get.assert_called_once_with(resourceId=DUMMY_WALLET_OBJECT_ID)

    @patch.dict(os.environ, {
        'GOOGLE_WALLET_SERVICE_ACCOUNT_CREDENTIALS': DUMMY_SERVICE_ACCOUNT_JSON,
        'GOOGLE_WALLET_ISSUER_ID': DUMMY_ISSUER_ID,
        'GOOGLE_WALLET_CLASS_ID': DUMMY_CLASS_ID
    })
    @patch('main.build')
    def test_api_error_on_insert_non_409(self, mock_build):
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.genericobject().insert().execute.side_effect = HttpError(
            resp=MagicMock(status=500), content=b'Server error on insert'
        )

        result_uri = create_google_wallet_object(self.sample_vcard_info, self.sample_vcard_id)
        self.assertIsNone(result_uri)
        mock_service.genericobject().get.assert_not_called() # Should not try to get if insert fails with non-409

    @patch.dict(os.environ, {
        'GOOGLE_WALLET_SERVICE_ACCOUNT_CREDENTIALS': DUMMY_SERVICE_ACCOUNT_JSON,
        'GOOGLE_WALLET_ISSUER_ID': DUMMY_ISSUER_ID,
        'GOOGLE_WALLET_CLASS_ID': DUMMY_CLASS_ID
    })
    @patch('main.build')
    def test_api_error_on_get_after_409(self, mock_build):
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.genericobject().insert().execute.side_effect = HttpError(
            resp=MagicMock(status=409), content=b'Object already exists'
        )
        mock_service.genericobject().get().execute.side_effect = HttpError(
            resp=MagicMock(status=500), content=b'Server error on get'
        )
        
        result_uri = create_google_wallet_object(self.sample_vcard_info, self.sample_vcard_id)
        self.assertIsNone(result_uri)

    @patch.dict(os.environ, { # Missing GOOGLE_WALLET_CLASS_ID
        'GOOGLE_WALLET_SERVICE_ACCOUNT_CREDENTIALS': DUMMY_SERVICE_ACCOUNT_JSON,
        'GOOGLE_WALLET_ISSUER_ID': DUMMY_ISSUER_ID
    })
    @patch('main.build')
    @patch('main.logging.error') # To check log messages
    def test_missing_env_vars_for_object(self, mock_log_error, mock_build):
        result_uri = create_google_wallet_object(self.sample_vcard_info, self.sample_vcard_id)
        self.assertIsNone(result_uri)
        mock_log_error.assert_any_call("Google Wallet environment variables not fully set (CREDENTIALS, ISSUER_ID, CLASS_ID).")
        mock_build.assert_not_called()

    @patch.dict(os.environ, {
        'GOOGLE_WALLET_SERVICE_ACCOUNT_CREDENTIALS': 'not json',
        'GOOGLE_WALLET_ISSUER_ID': DUMMY_ISSUER_ID,
        'GOOGLE_WALLET_CLASS_ID': DUMMY_CLASS_ID
    })
    @patch('main.build')
    @patch('main.logging.error')
    def test_invalid_credentials_json_for_object(self, mock_log_error, mock_build):
        result_uri = create_google_wallet_object(self.sample_vcard_info, self.sample_vcard_id)
        self.assertIsNone(result_uri)
        mock_log_error.assert_any_call("Error: GOOGLE_WALLET_SERVICE_ACCOUNT_CREDENTIALS is not valid JSON.")
        mock_build.assert_not_called()


if __name__ == '__main__':
    unittest.main()

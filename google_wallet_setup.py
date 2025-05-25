import os
import json
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials

def create_google_wallet_class():
    """
    Creates a new Google Wallet Generic Class for DOC Israel Membership Card.
    """
    try:
        # Load credentials and issuer ID from environment variables
        credentials_json = os.environ.get('GOOGLE_WALLET_SERVICE_ACCOUNT_CREDENTIALS')
        if not credentials_json:
            print("Error: GOOGLE_WALLET_SERVICE_ACCOUNT_CREDENTIALS environment variable not set.")
            return

        try:
            credentials_info = json.loads(credentials_json)
            credentials = Credentials.from_service_account_info(
                credentials_info,
                scopes=['https://www.googleapis.com/auth/wallet_object.issuer']
            )
        except json.JSONDecodeError:
            print(f"Error: GOOGLE_WALLET_SERVICE_ACCOUNT_CREDENTIALS is not valid JSON.")
            return

        issuer_id = os.environ.get('GOOGLE_WALLET_ISSUER_ID')
        if not issuer_id:
            print("Error: GOOGLE_WALLET_ISSUER_ID environment variable not set.")
            return

        class_suffix = "docIsraelMembershipCardV2" # Changed suffix to avoid conflicts if previous one exists
        class_id = f"{issuer_id}.{class_suffix}"

        # Construct the GenericClass resource
        class_definition = {
            "id": class_id,
            "classTemplateInfo": {
                "cardTemplateOverride": {
                    "cardRowTemplateInfos": [
                        {
                            "textModulesData": [
                                {
                                    "header": "MEMBER NAME",
                                    "body": "object.textModulesData['english_full_name'].value", # This will be mapped from the object
                                    "id": "english_full_name",
                                    "localizedHeader": {"defaultValue": {"language": "en-US", "value": "MEMBER NAME"}},
                                    "localizedBody": {"defaultValue": {"language": "en-US", "value": "Placeholder Name"}}
                                },
                                {
                                    "header": "MEMBER ID",
                                    "body": "object.textModulesData['ducati_member_code'].value", # Mapped from object
                                    "id": "ducati_member_code",
                                    "localizedHeader": {"defaultValue": {"language": "en-US", "value": "MEMBER ID"}},
                                    "localizedBody": {"defaultValue": {"language": "en-US", "value": "Placeholder ID"}}
                                },
                                {
                                    "header": "EXPIRES",
                                    "body": "object.textModulesData['membership_expiration'].value", # Mapped from object
                                    "id": "membership_expiration",
                                    "localizedHeader": {"defaultValue": {"language": "en-US", "value": "EXPIRES"}},
                                    "localizedBody": {"defaultValue": {"language": "en-US", "value": "Placeholder Date"}}
                                }
                            ]
                        },
                        {
                            "textModulesData": [
                                {
                                    "header": "MOTORCYCLE",
                                    "body": "object.textModulesData['motorcycle_model'].value", # Mapped from object
                                    "id": "motorcycle_model",
                                    "localizedHeader": {"defaultValue": {"language": "en-US", "value": "MOTORCYCLE"}},
                                    "localizedBody": {"defaultValue": {"language": "en-US", "value": "Placeholder Model"}}
                                },
                                {
                                    "header": "MEMBERSHIP YEAR",
                                    "body": "object.textModulesData['membership_year'].value", # Mapped from object
                                    "id": "membership_year",
                                    "localizedHeader": {"defaultValue": {"language": "en-US", "value": "MEMBERSHIP YEAR"}},
                                    "localizedBody": {"defaultValue": {"language": "en-US", "value": "Placeholder Year"}}
                                }
                            ]
                        }
                    ]
                },
                "logoImage": {
                    "sourceUri": {
                        "uri": "https://card.docil.co.il/logo.png"
                    },
                    "contentDescription": {
                         "defaultValue": {
                            "language": "en-US", # Using en-US for localization
                            "value": "DOC Israel Logo"
                        }
                    }
                },
                "hexBackgroundColor": "#cc0000"
            },
            "issuerName": "DOC Israel",
            "reviewStatus": "UNDER_REVIEW"
        }

        # Build the Wallet API service client
        service = build('walletobjects', 'v1', credentials=credentials)

        # Make an API call to create the class
        print(f"Attempting to create/update class with ID: {class_id}")
        
        try:
            # Try to get the class first
            service.genericclass().get(resourceId=class_id).execute()
            # If it exists, update it
            print(f"Class {class_id} already exists. Attempting to update.")
            response = service.genericclass().update(resourceId=class_id, body=class_definition).execute()
            print("Class update response:")
        except Exception as e:
            if e.resp.status == 404:
                # Class does not exist, insert it
                print(f"Class {class_id} does not exist. Attempting to insert.")
                response = service.genericclass().insert(body=class_definition).execute()
                print("Class creation response:")
            else:
                # Other error
                raise e
        
        print(json.dumps(response, indent=2))

    except Exception as e:
        print(f"An error occurred: {e}")
        if hasattr(e, 'content'):
            print(f"Error content: {e.content}")

if __name__ == '__main__':
    # This is for testing purposes.
    # Set environment variables before running this script directly.
    print("Attempting to create Google Wallet class directly from script...")
    print("Ensure GOOGLE_WALLET_SERVICE_ACCOUNT_CREDENTIALS and GOOGLE_WALLET_ISSUER_ID are set in your environment.")
    
    if not os.environ.get('GOOGLE_WALLET_SERVICE_ACCOUNT_CREDENTIALS') or not os.environ.get('GOOGLE_WALLET_ISSUER_ID'):
        print("\nWARNING: Environment variables for Google Wallet are not set.")
        print("Please set GOOGLE_WALLET_SERVICE_ACCOUNT_CREDENTIALS and GOOGLE_WALLET_ISSUER_ID to test this script.")
        print("Skipping class creation call.\n")
    else:
        create_google_wallet_class()

import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Microsoft Graph API credentials
MS_CLIENT_ID = os.environ.get('MS_CLIENT_ID')
MS_CLIENT_SECRET = os.environ.get('MS_CLIENT_SECRET')
MS_TENANT_ID = os.environ.get('MS_TENANT_ID')
TARGET_EMAIL = os.environ.get('TARGET_EMAIL')
INVOICE_RECIPIENT = os.environ.get('INVOICE_RECIPIENT')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

# Validate required credentials
def validate_credentials():
    missing = []
    for var in ['MS_CLIENT_ID', 'MS_CLIENT_SECRET', 'MS_TENANT_ID', 'TARGET_EMAIL', 'INVOICE_RECIPIENT', 'OPENAI_API_KEY']:
        if not globals().get(var):
            missing.append(var)
    
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
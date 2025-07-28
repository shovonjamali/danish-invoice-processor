import os
from pathlib import Path

# Base paths
BASE_DIR = Path(__file__).resolve().parent.parent
DOWNLOAD_DIR = os.environ.get('DOWNLOAD_PATH', os.path.join(BASE_DIR, 'downloads'))

# Local processing directories
LOCAL_PDF_DIR = os.path.join(BASE_DIR, 'local')
PROCESSED_PDF_DIR = os.path.join(BASE_DIR, 'processed')
OUTPUT_DIR = os.path.join(BASE_DIR, 'output')

# Ensure directories exist
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(LOCAL_PDF_DIR, exist_ok=True)
os.makedirs(PROCESSED_PDF_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Graph API settings
GRAPH_API_VERSION = 'v1.0'
GRAPH_BASE_URL = f'https://graph.microsoft.com/{GRAPH_API_VERSION}'

# Email processing settings
MAX_EMAILS_PER_BATCH = 5

# Invoice template path
INVOICE_TEMPLATE_PATH = os.path.join(BASE_DIR, 'templates', 'oioubl_template.xml')

# Output file retention
OUTPUT_FILES_MAX_AGE_DAYS = int(os.environ.get('OUTPUT_FILES_MAX_AGE_DAYS', 3))

USE_DEFAULT_CUSTOMER_ONLY = os.environ.get('USE_DEFAULT_CUSTOMER_ONLY', 'false').lower() == 'true'
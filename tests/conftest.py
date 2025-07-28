import pytest
import os
from unittest.mock import patch

@pytest.fixture(autouse=True)
def mock_env_vars():
    """Mock environment variables for all tests"""
    env_vars = {
        'MS_CLIENT_ID': 'test_client_id_12345',
        'MS_CLIENT_SECRET': 'test_secret_67890', 
        'MS_TENANT_ID': 'test_tenant_abcdef',
        'TARGET_EMAIL': 'test-source@company.com',
        'INVOICE_RECIPIENT': 'test-recipient@company.com',
        'OPENAI_API_KEY': 'sk-test-openai-key-123456',
        'USE_DEFAULT_CUSTOMER_ONLY': 'false',
        'LOG_LEVEL': 'DEBUG',
        'OUTPUT_FILES_MAX_AGE_DAYS': '3'
    }
    
    with patch.dict(os.environ, env_vars):
        yield

@pytest.fixture
def sample_invoice_data():
    """Sample invoice data for testing"""
    return {
        "invoice_number": "123456",
        "invoice_date": "2024-12-01",
        "supplier_name": "Test Supplier A/S",
        "supplier_vat": "DK12345678",
        "customer_name": "Test Customer A/S", 
        "customer_vat": "DK87654321",
        "total_amount": "1250.00",
        "currency": "DKK",
        "line_items": [
            {
                "description": "Test Product",
                "quantity": "2",
                "unit_price": "500.00",
                "amount": "1000.00"
            }
        ]
    }

@pytest.fixture
def sample_email():
    """Sample email data for testing"""
    return {
        "id": "email_123",
        "subject": "Invoice from Test Supplier",
        "hasAttachments": True,
        "from": {
            "emailAddress": {
                "address": "supplier@testcompany.com"
            }
        },
        "body": {
            "contentType": "HTML",
            "content": "<p>Please find attached invoice.</p>"
        }
    }

@pytest.fixture
def sample_attachment():
    """Sample email attachment data for testing"""
    return {
        "id": "attachment_456",
        "name": "invoice_123456.pdf",
        "contentType": "application/pdf",
        "size": 125430
    }

@pytest.fixture
def sample_markdown_content():
    """Sample markdown content extracted from PDF"""
    return """
# Invoice

**Invoice Number:** 123456
**Date:** 2024-12-01
**Supplier:** Test Supplier A/S
**CVR:** 12345678

## Line Items
- Product A: 2 x 500.00 DKK = 1000.00 DKK
- VAT 25%: 250.00 DKK
- **Total:** 1250.00 DKK
"""
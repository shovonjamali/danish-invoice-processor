import pytest
import os
from unittest.mock import patch, MagicMock, mock_open
from services.email_service import EmailService

@pytest.fixture
def email_service():
    with patch('services.email_service.GraphClient') as mock_client:
        service = EmailService()
        service.client = mock_client.return_value
        yield service

def test_get_unread_emails(email_service):
    # Mock data
    mock_emails = {
        "value": [
            {"id": "email1", "subject": "Test Email 1"},
            {"id": "email2", "subject": "Test Email 2"}
        ]
    }
   
    # Configure mock
    email_service.client.get.return_value = mock_emails
   
    # Call the method
    result = email_service.get_unread_emails()
   
    # Verify the result
    assert len(result) == 2
    assert result[0]["id"] == "email1"
    assert result[1]["subject"] == "Test Email 2"
   
    # Verify the API call - simplified
    email_service.client.get.assert_called_once()
    args = email_service.client.get.call_args[0]
    assert "/users/" in args[0]

def test_get_unread_emails_empty(email_service):
    # Test when no emails are found
    mock_emails = {"value": []}
    email_service.client.get.return_value = mock_emails
    
    result = email_service.get_unread_emails()
    
    assert len(result) == 0
    assert result == []

def test_get_email_attachments(email_service):
    # Test attachment retrieval
    mock_attachments = {
        "value": [
            {
                "id": "att1", 
                "name": "invoice.pdf", 
                "contentType": "application/pdf"
            },
            {
                "id": "att2", 
                "name": "receipt.xml", 
                "contentType": "application/xml"
            }
        ]
    }
    email_service.client.get.return_value = mock_attachments
    
    result = email_service.get_email_attachments("email_id")
    
    assert len(result) == 2
    assert result[0]["name"] == "invoice.pdf"
    assert result[1]["contentType"] == "application/xml"
    
    # Verify API call
    email_service.client.get.assert_called_once()
    args = email_service.client.get.call_args[0]
    assert "attachments" in args[0]

def test_get_email_attachments_none(email_service):
    # Test when email has no attachments
    mock_attachments = {"value": []}
    email_service.client.get.return_value = mock_attachments
    
    result = email_service.get_email_attachments("email_id")
    
    assert len(result) == 0

def test_download_attachment(email_service):
    # Mock binary content
    mock_content = b"PDF file content"
    email_service.client.get_binary.return_value = mock_content
    
    # Mock file operations
    with patch("builtins.open", mock_open()) as mock_file:
        with patch("os.path.join", return_value="test_invoice.pdf"):
            result = email_service.download_attachment("email_id", "att_id", "test_invoice.pdf")
    
    # Verify file was written
    mock_file.assert_called_once_with("test_invoice.pdf", 'wb')
    mock_file().write.assert_called_once_with(mock_content)
    assert result == "test_invoice.pdf"

def test_download_attachment_no_filename(email_service):
    # Test downloading attachment without provided filename
    mock_content = b"PDF content"
    mock_attachment_info = {"name": "auto_generated.pdf"}
    
    email_service.client.get_binary.return_value = mock_content
    email_service.client.get.return_value = mock_attachment_info
    
    with patch("builtins.open", mock_open()) as mock_file:
        with patch("os.path.join", return_value="auto_generated.pdf"):
            result = email_service.download_attachment("email_id", "att_id")
    
    # Should get filename from attachment info
    assert result == "auto_generated.pdf"

def test_mark_as_read(email_service):
    # Mock data
    email_id = "test_email_id"
   
    # Call the method
    result = email_service.mark_as_read(email_id)
   
    # Verify the result
    assert result is True
   
    # Verify the API call - simplified
    email_service.client.patch.assert_called_once()
    args = email_service.client.patch.call_args[0]
    assert f"/users/{email_service.target_email}/messages/{email_id}" in args[0]

@patch.dict('os.environ', {'INVOICE_RECIPIENT': 'test@company.com'})
@patch("os.path.exists", return_value=True)
def test_send_invoice_success(mock_exists, email_service):
    # Mock successful email sending
    email_service.client.post.return_value = None
    
    # Mock file reading
    with patch("builtins.open", mock_open(read_data=b"XML content")):
        with patch("base64.b64encode", return_value=b"encoded_content"):
            with patch("os.path.basename", return_value="invoice.xml"):
                result = email_service.send_invoice(
                    "test_invoice.xml", 
                    {"invoice_number": "123", "direct_xml": False}, 
                    {"subject": "Test"}, 
                    {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
                )
    
    assert result is True
    email_service.client.post.assert_called_once()

@patch.dict('os.environ', {}, clear=True)
def test_send_invoice_missing_recipient(email_service):
    # Test error handling when recipient is missing
    result = email_service.send_invoice(
        "test_invoice.xml", 
        {"invoice_number": "123"}, 
        {"subject": "Test"}
    )
    
    assert result is False

@patch.dict('os.environ', {'INVOICE_RECIPIENT': 'test@company.com'})
@patch("os.path.exists", return_value=False)
def test_send_invoice_file_not_found(mock_exists, email_service):
    # Test when invoice file doesn't exist
    result = email_service.send_invoice(
        "nonexistent.xml", 
        {"invoice_number": "123"}, 
        {"subject": "Test"}
    )
    
    assert result is False

@patch.dict('os.environ', {'INVOICE_RECIPIENT': 'test@company.com'})
@patch("os.path.exists", return_value=True)
def test_send_invoice_direct_xml(mock_exists, email_service):
    # Test sending direct XML (forwarded email)
    email_service.client.post.return_value = None
    
    with patch("builtins.open", mock_open(read_data=b"XML content")):
        with patch("base64.b64encode", return_value=b"encoded_content"):
            with patch("os.path.basename", return_value="invoice.xml"):
                result = email_service.send_invoice(
                    "direct_invoice.xml", 
                    {"invoice_number": "456", "direct_xml": True}, 
                    {"subject": "Direct XML"}
                )
    
    assert result is True
    
    # Verify the email was sent
    email_service.client.post.assert_called_once()
    call_args = email_service.client.post.call_args
    
    # Check that it's a forwarded email (different subject pattern)
    message_data = call_args[0][1]
    assert "Forwarded" in message_data["message"]["subject"]
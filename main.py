import logging
import os
import shutil
import traceback
import argparse
from datetime import datetime, timedelta
import base64
from config.credentials import validate_credentials
from config.settings import DOWNLOAD_DIR, OUTPUT_FILES_MAX_AGE_DAYS
from services.email_service import EmailService
from services.pdf_service import PDFService
from services.invoice_service import InvoiceService
from services.local_pdf_service import LocalPDFService
from utils.file_utils import safe_filename
from utils.token_tracker import get_token_usage, get_cost_estimate, reset_counters
from dotenv import load_dotenv
import re

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO")),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


def convert_pdf_to_markdown(file_path):
    logger.info(f"Converting PDF to markdown: {file_path}")
    pdf_service = PDFService()
    markdown_content = pdf_service.convert_to_markdown(file_path)
    
    # Log complete content for difficult cases
    logger.info(f"PDF converted to markdown, length: {len(markdown_content)} characters")
    
    # Find all occurrences of specific keywords
    keywords = ["Faktura", "Nummer", "Fakturakonto"]
    for keyword in keywords:
        positions = [m.start() for m in re.finditer(keyword, markdown_content)]
        if positions:
            logger.info(f"Found '{keyword}' at positions: {positions}")
            for pos in positions:
                context = markdown_content[max(0, pos-20):min(len(markdown_content), pos+80)]
                logger.info(f"Context around '{keyword}': {context}")
    
    # Log the entire content line by line to help with debugging
    lines = markdown_content.split('\n')
    logger.info(f"Total lines in content: {len(lines)}")
    for i, line in enumerate(lines[:50]):  # Log first 50 lines
        logger.info(f"Line {i}: {line}")
    
    return markdown_content

def generate_invoice(markdown_content, invoice_service):
    logger.info("Generating invoice from markdown content")
    return invoice_service.generate_invoice(markdown_content)

def send_invoice_email(invoice_file_path, invoice_data, original_email, token_usage=None):
    email_service = EmailService()
    return email_service.send_invoice(invoice_file_path, invoice_data, original_email, token_usage)

def forward_email_directly(email):
    """Forward an email directly without any modification"""
    try:
        email_service = EmailService()
        recipient_email = os.environ.get('INVOICE_RECIPIENT')
        
        if not recipient_email:
            logger.error("INVOICE_RECIPIENT not defined in environment variables")
            return False
        
        # Get original email details
        original_subject = email.get("subject", "No subject")
        original_body = email.get("body", {})
        email_id = email.get("id")
        
        # Create a message that preserves the original email as closely as possible
        message = {
            "message": {
                "subject": original_subject,  # Keep original subject
                "body": original_body,        # Keep original body with same content type
                "toRecipients": [
                    {
                        "emailAddress": {
                            "address": recipient_email
                        }
                    }
                ]
            }
        }
        
        # If the original has a CC, preserve it (optional)
        if "ccRecipients" in email:
            message["message"]["ccRecipients"] = email["ccRecipients"]
        
        # Get original attachments if available
        attachments = email_service.get_email_attachments(email_id)
        
        # If there are attachments, add them to the forwarded email
        if attachments:
            forwarded_attachments = []
            
            for attachment in attachments:
                # Download the attachment
                attachment_id = attachment.get("id")
                attachment_name = attachment.get("name", "attachment")
                content_type = attachment.get("contentType", "application/octet-stream")
                
                # Get attachment content
                attachment_content = email_service.client.get_binary(
                    f"/users/{email_service.target_email}/messages/{email_id}/attachments/{attachment_id}/$value"
                )
                
                # Encode for the email
                content_bytes = base64.b64encode(attachment_content).decode('utf-8')
                
                # Add to forwarded attachments
                forwarded_attachments.append({
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": attachment_name,
                    "contentType": content_type,
                    "contentBytes": content_bytes
                })
                
            # Add attachments to the message
            message["message"]["attachments"] = forwarded_attachments
        
        # Send the email
        endpoint = f"/users/{email_service.target_email}/sendMail"
        email_service.client.post(endpoint, message)
        
        logger.info(f"Email forwarded to {recipient_email} without modification")
        return True
        
    except Exception as e:
        logger.error(f"Error forwarding email: {e}", exc_info=True)
        return False

def process_attachment(attachment, email_id, email_data, temp_dir, invoice_service):
    try:
        content_type = attachment.get("contentType", "")
        attachment_id = attachment.get("id")
        original_filename = attachment.get("name", "attachment")
        safe_name = safe_filename(original_filename)
        
        logger.info(f"Processing attachment: {safe_name} (type: {content_type})")
        
        # Check if it's an XML file - trigger direct email forwarding
        if content_type == "application/xml" or content_type == "text/xml" or original_filename.lower().endswith('.xml'):
            logger.info(f"Found XML attachment: {safe_name} - will forward entire email directly")
            # Return special status to indicate the entire email should be forwarded
            return "FORWARD_ENTIRE_EMAIL"
            
        # Skip non-PDF attachments that aren't XML
        if content_type != "application/pdf":
            logger.info(f"Skipping non-PDF, non-XML attachment: {safe_name}")
            return False
            
        # Process PDF as before
        email_service = EmailService()  # Initialize email service
        temp_file_path = os.path.join(temp_dir, safe_name)
        
        # Download attachment to temp directory
        logger.info(f"Downloading PDF attachment to: {temp_file_path}")
        email_service.download_attachment(email_id, attachment_id, temp_file_path)
        
        # Convert PDF to markdown
        markdown_content = convert_pdf_to_markdown(temp_file_path)
        if not markdown_content:
            logger.warning(f"No markdown content extracted from PDF: {safe_name}")
            return False
            
        # Generate invoice from markdown
        invoice_file_path, invoice_data, token_usage = generate_invoice(markdown_content, invoice_service)

        if not invoice_file_path or not invoice_data:
            logger.warning("Failed to generate invoice")
            return False 

        # Get token usage from the global tracker
        token_usage = get_token_usage()

        # Send invoice email
        result = send_invoice_email(invoice_file_path, invoice_data, email_data, token_usage)
        
        return result
        
    except Exception as e:
        logger.error(f"Error processing attachment: {e}")
        logger.debug(traceback.format_exc())
        return False

def process_single_email(email, temp_dir, invoice_service):
    try:
        email_id = email["id"]
        subject = email["subject"]
        from_email = email.get("from", {}).get("emailAddress", {}).get("address", "unknown")
        
        logger.info(f"Processing email: {subject} from {from_email}")
        
        # Initialize email service
        email_service = EmailService()
        
        # If email doesn't have attachments, forward it directly
        if not email.get("hasAttachments", False):
            logger.info(f"Email has no attachments, forwarding directly: {subject}")
            
            # Forward the email directly
            result = forward_email_directly(email)
            
            # Mark as read if successfully forwarded
            if result:
                email_service.mark_as_read(email_id)    # Comment on dev
                logger.info(f"Successfully forwarded email: {subject}")
                return True
            else:
                logger.warning(f"Failed to forward email: {subject}")
                return False
        
        # Get attachments for emails that have them
        attachments = email_service.get_email_attachments(email_id)
        
        if not attachments:
            logger.info(f"No attachments found for email: {subject}")
            # Forward the email directly since it claims to have attachments but none were found
            result = forward_email_directly(email)
            
            if result:
                email_service.mark_as_read(email_id)    # Comment on dev
                logger.info(f"Successfully forwarded email without attachments: {subject}")
                return True
            else:
                logger.warning(f"Failed to forward email without attachments: {subject}")
                return False
        
        # First check if any attachment is XML - if so, forward the entire email directly
        for attachment in attachments:
            content_type = attachment.get("contentType", "")
            filename = attachment.get("name", "")
            
            if (content_type == "application/xml" or content_type == "text/xml" or 
                filename.lower().endswith('.xml')):
                logger.info(f"Found XML attachment, forwarding entire email directly: {subject}")
                result = forward_email_directly(email)
                
                if result:
                    email_service.mark_as_read(email_id)    # Comment on dev
                    logger.info(f"Successfully forwarded email with XML: {subject}")
                    return True
                else:
                    logger.warning(f"Failed to forward email with XML: {subject}")
                    return False
            
        # Process each attachment (PDFs only at this point)
        success = False
        for attachment in attachments:
            attachment_result = process_attachment(
                attachment, email_id, email, temp_dir, invoice_service
            )
            
            # Special handling for XML forwarding flag
            if attachment_result == "FORWARD_ENTIRE_EMAIL":
                logger.info(f"XML detected during processing, forwarding entire email: {subject}")
                return forward_email_directly(email)
                
            # If any attachment is processed successfully, consider the email processed
            if attachment_result:
                success = True
                
        # Mark email as read only if successfully processed
        if success:
            email_service.mark_as_read(email_id)    # Comment on dev
            logger.info(f"Successfully processed email: {subject}")
        else:
            logger.warning(f"Failed to process email: {subject}")
            
        return success
        
    except Exception as e:
        logger.error(f"Error processing email: {e}")
        logger.debug(traceback.format_exc())
        return False

def clean_output_directory(max_age_days=7):
    try:
        output_dir = os.path.join(os.getcwd(), "output")
        if not os.path.exists(output_dir):
            return
            
        logger.info(f"Cleaning up output directory: {output_dir}")
        current_time = datetime.now()
        max_age = timedelta(days=max_age_days)
        
        # Check each file in the output directory
        for filename in os.listdir(output_dir):
            file_path = os.path.join(output_dir, filename)
            
            # Skip directories
            if os.path.isdir(file_path):
                continue
                
            # Check file age
            file_age = current_time - datetime.fromtimestamp(os.path.getmtime(file_path))
            if file_age > max_age:
                try:
                    os.remove(file_path)
                    logger.info(f"Removed old file: {filename}")
                except Exception as e:
                    logger.error(f"Error removing file {filename}: {e}")
    
    except Exception as cleanup_error:
        logger.error(f"Error cleaning up output directory: {cleanup_error}")

def process_local_pdfs():
    """Process PDF files from local directory"""
    
    # Reset token counters at the start of processing
    reset_counters()
    
    try:
        logger.info("Starting local PDF processing")
        
        # Initialize local PDF service
        local_service = LocalPDFService()
        
        # Process all PDFs in the local directory
        results = local_service.process_all_pdfs()
        
        # Print summary
        print("\n" + "="*60)
        print("LOCAL PDF PROCESSING SUMMARY")
        print("="*60)
        print(f"Total files processed: {results['total_files']}")
        print(f"Successful: {results['successful']}")
        print(f"Failed: {results['failed']}")
        
        if results.get('error'):
            print(f"Error: {results['error']}")
        
        # Print token usage
        token_usage = results['total_token_usage']
        if token_usage['total_tokens'] > 0:
            print(f"\nToken Usage:")
            print(f"  Prompt tokens: {token_usage['prompt_tokens']:,}")
            print(f"  Completion tokens: {token_usage['completion_tokens']:,}")
            print(f"  Total tokens: {token_usage['total_tokens']:,}")
            
            # Calculate cost
            prompt_cost = (token_usage['prompt_tokens'] / 1000) * 0.0015
            completion_cost = (token_usage['completion_tokens'] / 1000) * 0.002
            total_cost = prompt_cost + completion_cost
            print(f"  Estimated cost: ${total_cost:.4f}")
        
        print("="*60)
        
        if results['successful'] > 0:
            print(f"\nGenerated XML files can be found in the 'output' directory")
            print(f"Processed PDF files have been moved to the 'processed' directory")
        
        logger.info("Local PDF processing completed")
        
    except Exception as e:
        logger.error(f"Error in local PDF processing: {e}")
        logger.debug(traceback.format_exc())
        print(f"\nError: {e}")
        raise
    finally:
        # Clean up old output files
        clean_output_directory(max_age_days=OUTPUT_FILES_MAX_AGE_DAYS)

def process_emails():
    """Main function to process emails"""

    # Reset token counters at the start of processing
    reset_counters()
    
    # Create a session-specific temp directory
    session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_root = os.path.join(DOWNLOAD_DIR, f"session_{session_id}")

    # Initialize invoice service
    invoice_service = InvoiceService()
    
    try:
        # Validate credentials before starting
        validate_credentials()
        
        # Create temp directory for this processing session
        os.makedirs(temp_root, exist_ok=True)
        logger.info(f"Created temporary directory: {temp_root}")
        
        # Initialize services
        email_service = EmailService()
        
        # Get unread emails
        emails = email_service.get_unread_emails()
        
        if not emails:
            logger.info("No unread emails to process")
            return
            
        logger.info(f"Found {len(emails)} unread emails to process")
        
        # Process each email in a separate temp directory
        for index, email in enumerate(emails):
            # Create email-specific temp directory
            email_temp_dir = os.path.join(temp_root, f"email_{index}")
            os.makedirs(email_temp_dir, exist_ok=True)
            
            try:
                # process_single_email(email, email_temp_dir)
                process_single_email(email, email_temp_dir, invoice_service)
            except Exception as e:
                logger.error(f"Failed to process email {index}: {e}")
                # Continue with next email
                
        logger.info("Email processing completed")

        # Log token usage from the global tracker
        usage = get_token_usage()
        
        logger.info("Token Usage Summary:")
        logger.info(f"Prompt tokens: {usage['prompt_tokens']}")
        logger.info(f"Completion tokens: {usage['completion_tokens']}")
        logger.info(f"Total tokens: {usage['total_tokens']}")
        logger.info(f"Estimated OpenAI API cost: ${get_cost_estimate():.4f}")
        
    except Exception as e:
        logger.error(f"Error in email processing: {e}")
        logger.debug(traceback.format_exc())
        raise
    finally:
        # Clean up temporary files
        try:
            if os.path.exists(temp_root):
                logger.info(f"Cleaning up temporary directory: {temp_root}")
                shutil.rmtree(temp_root)    # Comment on dev
        except Exception as cleanup_error:
            logger.error(f"Error cleaning up temp directory: {cleanup_error}")

        # Clean up old output files
        clean_output_directory(max_age_days=OUTPUT_FILES_MAX_AGE_DAYS)

def main():
    """Application entry point with support for local and email processing"""
    
    # Set up argument parser
    parser = argparse.ArgumentParser(description='Invoice Processing System')
    parser.add_argument('--local', action='store_true', 
                       help='Process PDF files from local directory instead of emails')
    
    args = parser.parse_args()
    
    if args.local:
        # Process local PDFs
        process_local_pdfs()
    else:
        # Default behavior - process emails
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                process_emails()
                break
            except Exception as e:
                retry_count += 1
                logger.error(f"Attempt {retry_count} failed: {e}")
                if retry_count >= max_retries:
                    logger.critical("Max retries exceeded, giving up")
                    raise
                
if __name__ == "__main__":
    main()
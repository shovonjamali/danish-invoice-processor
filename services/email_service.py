import os
import logging
from services.graph_client import GraphClient
from config.credentials import TARGET_EMAIL
from config.settings import MAX_EMAILS_PER_BATCH
import base64

logger = logging.getLogger(__name__)

class EmailService:
    """Service for interacting with emails via Graph API"""
    
    def __init__(self):
        self.client = GraphClient()
        self.target_email = TARGET_EMAIL
    
    def get_unread_emails(self, limit=MAX_EMAILS_PER_BATCH):
        endpoint = f"/users/{self.target_email}/messages"
        
        params = {
            "$filter": "isRead eq false",
            "$top": limit,
            "$select": "id,subject,receivedDateTime,hasAttachments,from,body,ccRecipients"
        }
        
        response = self.client.get(endpoint, params)
        emails = response.get("value", [])
        
        logger.info(f"Found {len(emails)} unread emails")
        return emails
    
    def get_email_attachments(self, email_id):
        endpoint = f"/users/{self.target_email}/messages/{email_id}/attachments"
        
        response = self.client.get(endpoint)
        attachments = response.get("value", [])
        
        logger.info(f"Found {len(attachments)} attachments for email {email_id}")
        return attachments
    
    def download_attachment(self, email_id, attachment_id, filename=None):
        endpoint = f"/users/{self.target_email}/messages/{email_id}/attachments/{attachment_id}/$value"
        
        content = self.client.get_binary(endpoint)
        
        if not filename:
            # Get attachment info to get the filename
            attachment_info = self.client.get(
                f"/users/{self.target_email}/messages/{email_id}/attachments/{attachment_id}"
            )
            filename = attachment_info.get("name", f"attachment_{attachment_id}")
        
        # file_path = os.path.join(DOWNLOAD_DIR, filename)
        file_path = os.path.join(filename)
        
        with open(file_path, 'wb') as file:
            file.write(content)
        
        logger.info(f"Downloaded attachment to {file_path}")
        return file_path
    
    def mark_as_read(self, email_id):
        endpoint = f"/users/{self.target_email}/messages/{email_id}"
        
        data = {
            "isRead": True
        }
        
        self.client.patch(endpoint, data)
        logger.info(f"Marked email {email_id} as read")
        return True

    def _create_forwarded_xml_email_body(self, invoice_number, processing_date):
        """Create email body template for forwarded XML invoices"""
        return f"""
        <div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 500px; margin: 0 auto;">
            <div style="text-align: center; padding: 40px 20px;">
                <div style="display: inline-block; background: #fef3c7; padding: 20px; border-radius: 50%; margin-bottom: 20px;">
                    <span style="font-size: 30px;">📨</span>
                </div>
                
                <h2 style="color: #1f2937; margin: 0 0 10px 0; font-weight: 600;">Invoice Forwarded</h2>
                <p style="color: #6b7280; margin: 0 0 30px 0;">XML file received and forwarded directly</p>
                
                <div style="background: white; border: 1px solid #e5e7eb; border-radius: 12px; padding: 25px; text-align: left; margin-bottom: 30px;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 15px; padding-bottom: 15px; border-bottom: 1px solid #f3f4f6;">
                        <span style="color: #374151; font-weight: 500;">Invoice #</span>
                        <span style="color: #1f2937; font-family: monospace;">{invoice_number}</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 15px; padding-bottom: 15px; border-bottom: 1px solid #f3f4f6;">
                        <span style="color: #374151; font-weight: 500;">Received</span>
                        <span style="color: #1f2937;">{processing_date}</span>
                    </div>
                    <div style="display: flex; justify-content: space-between;">
                        <span style="color: #374151; font-weight: 500;">Status</span>
                        <span style="color: #d97706; font-weight: 600;">📨 Forwarded</span>
                    </div>
                </div>
                
                <p style="font-size: 14px; color: #6b7280; line-height: 1.5;">
                    This XML invoice was received and forwarded directly without processing as it was already in the correct format.
                </p>
                
                <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #e5e7eb;">
                    <p style="font-size: 12px; color: #9ca3af; margin: 0;">
                        Powered by AI Invoice Assistant
                    </p>
                </div>
            </div>
        </div>
        """

    def _create_processed_pdf_email_body(self, invoice_number, processing_date, token_usage=None):
        """Create email body template for processed PDF invoices"""
        body = f"""
        <div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 500px; margin: 0 auto;">
            <div style="text-align: center; padding: 40px 20px;">
                <div style="display: inline-block; background: #f0f9ff; padding: 20px; border-radius: 50%; margin-bottom: 20px;">
                    <span style="font-size: 30px;">📄</span>
                </div>
                
                <h2 style="color: #1f2937; margin: 0 0 10px 0; font-weight: 600;">Invoice Processed</h2>
                <p style="color: #6b7280; margin: 0 0 30px 0;">Your OIOUBL file is ready</p>
                
                <div style="background: white; border: 1px solid #e5e7eb; border-radius: 12px; padding: 25px; text-align: left; margin-bottom: 30px;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 15px; padding-bottom: 15px; border-bottom: 1px solid #f3f4f6;">
                        <span style="color: #374151; font-weight: 500;">Invoice #</span>
                        <span style="color: #1f2937; font-family: monospace;">{invoice_number}</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 15px; padding-bottom: 15px; border-bottom: 1px solid #f3f4f6;">
                        <span style="color: #374151; font-weight: 500;">Processed</span>
                        <span style="color: #1f2937;">{processing_date}</span>
                    </div>
                    <div style="display: flex; justify-content: space-between;">
                        <span style="color: #374151; font-weight: 500;">Status</span>
                        <span style="color: #059669; font-weight: 600;">✓ Ready</span>
                    </div>
                </div>
                
                <p style="font-size: 14px; color: #6b7280; line-height: 1.5;">
                    The processed OIOUBL XML file is attached and ready for use in your accounting system.
                </p>
        """
        
        # Add token usage information if provided
        if token_usage:
            prompt_tokens = token_usage.get('prompt_tokens', 0)
            completion_tokens = token_usage.get('completion_tokens', 0)
            total_tokens = token_usage.get('total_tokens', 0)
            
            # Calculate cost
            prompt_cost = (prompt_tokens / 1000) * 0.0015
            completion_cost = (completion_tokens / 1000) * 0.002
            total_cost = prompt_cost + completion_cost
            
            body += f"""
                <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 20px; margin: 20px 0; text-align: left;">
                    <h4 style="color: #475569; margin: 0 0 15px 0; font-size: 14px; font-weight: 600;">⚡ Processing Stats</h4>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; font-size: 12px;">
                        <div style="color: #64748b;">Prompt tokens:</div>
                        <div style="color: #1e293b; font-family: monospace;">{prompt_tokens:,}</div>
                        <div style="color: #64748b;">Completion tokens:</div>
                        <div style="color: #1e293b; font-family: monospace;">{completion_tokens:,}</div>
                        <div style="color: #64748b;">Total tokens:</div>
                        <div style="color: #1e293b; font-family: monospace;">{total_tokens:,}</div>
                        <div style="color: #64748b; font-weight: 600;">Estimated cost:</div>
                        <div style="color: #059669; font-family: monospace; font-weight: 600;">${total_cost:.4f}</div>
                    </div>
                </div>
            """
        
        # Close the template
        body += """
                <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #e5e7eb;">
                    <p style="font-size: 12px; color: #9ca3af; margin: 0;">
                        Automated by Invoice Processing System
                    </p>
                </div>
            </div>
        </div>
        """
        
        return body

    def send_invoice(self, invoice_file_path, invoice_data, original_email, token_usage=None):
        try:
            from datetime import datetime
            
            # Check if this is a direct XML (no processing required)
            is_direct_xml = invoice_data.get('direct_xml', False)
            invoice_number = invoice_data.get('invoice_number', 'unknown')
            
            logger.info(f"Sending invoice email for: {invoice_number} (direct XML: {is_direct_xml})")
            
            # Check if the invoice file exists
            if not os.path.exists(invoice_file_path):
                logger.error(f"Invoice file not found: {invoice_file_path}")
                return False
            
            # Get the specific recipient email from environment variables
            recipient_email = os.environ.get('INVOICE_RECIPIENT')
            
            if not recipient_email:
                logger.error("INVOICE_RECIPIENT not defined in environment variables")
                return False
                
            # Read the invoice file content
            with open(invoice_file_path, 'rb') as file:
                attachment_content = base64.b64encode(file.read()).decode('utf-8')
            
            # Get the file name from the path
            file_name = os.path.basename(invoice_file_path)
            
            # Create a descriptive subject line
            subject = f"{'Forwarded' if is_direct_xml else 'Your invoice is ready'}: {invoice_number}"
            
            # Get processing date
            processing_date = datetime.now().strftime("%B %d, %Y at %H:%M")
            
            # Create email body using appropriate template method
            if is_direct_xml:
                body = self._create_forwarded_xml_email_body(invoice_number, processing_date)
            else:
                body = self._create_processed_pdf_email_body(invoice_number, processing_date, token_usage)
        
            # Prepare the email message
            message = {
                "message": {
                    "subject": subject,
                    "body": {
                        "contentType": "HTML",
                        "content": body
                    },
                    "toRecipients": [
                        {
                            "emailAddress": {
                                "address": recipient_email
                            }
                        }
                    ],
                    "attachments": [
                        {
                            "@odata.type": "#microsoft.graph.fileAttachment",
                            "name": file_name,
                            "contentType": "application/xml",
                            "contentBytes": attachment_content
                        }
                    ]
                }
            }
            
            # Send the email using Microsoft Graph API
            endpoint = f"/users/{self.target_email}/sendMail"
            
            self.client.post(endpoint, message)
            
            logger.info(f"Invoice email sent to {recipient_email} with attachment: {file_name}")
            return True
        
        except Exception as e:
            logger.error(f"Error sending invoice email: {e}", exc_info=True)
            return False
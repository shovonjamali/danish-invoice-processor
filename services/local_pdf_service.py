import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Tuple
from config.settings import LOCAL_PDF_DIR, PROCESSED_PDF_DIR, OUTPUT_DIR
from services.pdf_service import PDFService
from services.invoice_service import InvoiceService

logger = logging.getLogger(__name__)

class LocalPDFService:
    """Service for processing PDF files from local directory"""
    
    def __init__(self):
        logger.info("Initializing Local PDF Service")
        self.pdf_service = PDFService()
        self.invoice_service = InvoiceService()
        
    def get_pdf_files(self) -> List[str]:
        """Get all PDF files from the local directory"""
        try:
            pdf_files = []
            local_path = Path(LOCAL_PDF_DIR)
            
            if not local_path.exists():
                logger.warning(f"Local PDF directory does not exist: {LOCAL_PDF_DIR}")
                return pdf_files
                
            # Find all PDF files
            for file_path in local_path.glob("*.pdf"):
                if file_path.is_file():
                    pdf_files.append(str(file_path))
                    
            logger.info(f"Found {len(pdf_files)} PDF files in local directory")
            return sorted(pdf_files)  # Sort for consistent processing order
            
        except Exception as e:
            logger.error(f"Error getting PDF files: {e}")
            return []
    
    def process_single_pdf(self, pdf_path: str) -> Tuple[bool, str, dict]:
        """
        Process a single PDF file
        
        Returns:
            Tuple of (success, message, token_usage)
        """
        try:
            filename = os.path.basename(pdf_path)
            logger.info(f"Processing PDF: {filename}")
            
            # Convert PDF to markdown
            markdown_content = self.pdf_service.convert_to_markdown(pdf_path)
            if not markdown_content:
                error_msg = f"Failed to extract content from PDF: {filename}"
                logger.error(error_msg)
                return False, error_msg, {}
            
            # Generate invoice from markdown
            invoice_file_path, invoice_data, token_usage = self.invoice_service.generate_invoice(markdown_content)
            
            if not invoice_file_path or not invoice_data:
                error_msg = f"Failed to generate invoice from PDF: {filename}"
                logger.error(error_msg)
                return False, error_msg, token_usage or {}
            
            # Move processed PDF to processed directory
            self.move_to_processed(pdf_path)
            
            success_msg = f"Successfully processed {filename} -> {os.path.basename(invoice_file_path)}"
            logger.info(success_msg)
            
            return True, success_msg, token_usage or {}
            
        except Exception as e:
            error_msg = f"Error processing {os.path.basename(pdf_path)}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return False, error_msg, {}
    
    def move_to_processed(self, pdf_path: str) -> str:
        """Move PDF file to processed directory"""
        try:
            filename = os.path.basename(pdf_path)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Create unique filename to avoid conflicts
            name, ext = os.path.splitext(filename)
            processed_filename = f"{name}_{timestamp}{ext}"
            processed_path = os.path.join(PROCESSED_PDF_DIR, processed_filename)
            
            # Move the file
            shutil.move(pdf_path, processed_path)
            logger.info(f"Moved {filename} to processed directory as {processed_filename}")
            
            return processed_path
            
        except Exception as e:
            logger.error(f"Error moving file to processed directory: {e}")
            raise
    
    def process_all_pdfs(self) -> dict:
        """
        Process all PDF files in the local directory
        
        Returns:
            Dictionary with processing statistics
        """
        try:
            pdf_files = self.get_pdf_files()
            
            if not pdf_files:
                logger.info("No PDF files found to process")
                return {
                    "total_files": 0,
                    "successful": 0,
                    "failed": 0,
                    "results": [],
                    "total_token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                }
            
            results = []
            successful = 0
            failed = 0
            total_token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            
            logger.info(f"Starting to process {len(pdf_files)} PDF files")
            
            for pdf_path in pdf_files:
                filename = os.path.basename(pdf_path)
                success, message, token_usage = self.process_single_pdf(pdf_path)
                
                # Accumulate token usage
                for key in total_token_usage:
                    total_token_usage[key] += token_usage.get(key, 0)
                
                result = {
                    "filename": filename,
                    "success": success,
                    "message": message,
                    "token_usage": token_usage
                }
                results.append(result)
                
                if success:
                    successful += 1
                    print(f"✓ {message}")
                else:
                    failed += 1
                    print(f"✗ {message}")
            
            # Summary statistics
            stats = {
                "total_files": len(pdf_files),
                "successful": successful,
                "failed": failed,
                "results": results,
                "total_token_usage": total_token_usage
            }
            
            logger.info(f"Processing completed: {successful} successful, {failed} failed")
            return stats
            
        except Exception as e:
            logger.error(f"Error in process_all_pdfs: {e}", exc_info=True)
            return {
                "total_files": 0,
                "successful": 0,
                "failed": 0,
                "results": [],
                "error": str(e),
                "total_token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            }
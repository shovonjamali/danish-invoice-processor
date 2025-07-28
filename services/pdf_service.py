import logging
from markitdown import MarkItDown

logger = logging.getLogger(__name__)

class PDFService:
    """Service for processing PDF files and extracting data"""
    
    def __init__(self, enable_plugins=False):
        logger.info("Initializing PDF Service")
        # Initialize the MarkItDown object during service initialization
        self.markitdown = MarkItDown(enable_plugins=enable_plugins)
    
    def convert_to_markdown(self, file_path: str) -> str:
        try:
            logger.info(f"Converting PDF to markdown using MarkItDown: {file_path}")
            
            # Use MarkItDown to convert PDF to markdown
            result = self.markitdown.convert(file_path)
            
            # Get the text content (markdown)
            markdown_content = result.text_content
            
            logger.info(f"PDF successfully converted to markdown, length: {len(markdown_content)} characters")
            
            return markdown_content
            
        except Exception as e:
            logger.error(f"Error converting PDF to markdown: {e}", exc_info=True)
            return ""
import pytest
from unittest.mock import patch, MagicMock, mock_open
from services.pdf_service import PDFService

@pytest.fixture
def pdf_service():
    # Mock MarkItDown at the service initialization level
    with patch('services.pdf_service.MarkItDown') as mock_markitdown:
        mock_instance = MagicMock()
        mock_markitdown.return_value = mock_instance
        service = PDFService()
        service.markitdown = mock_instance
        yield service

def test_convert_to_markdown_success(pdf_service):
    # Mock MarkItDown response
    mock_result = MagicMock()
    mock_result.text_content = "# Invoice\nInvoice Number: 123\nAmount: 1000 DKK"
    
    # Configure the mock instance
    pdf_service.markitdown.convert.return_value = mock_result
    
    # Test conversion
    result = pdf_service.convert_to_markdown("test_invoice.pdf")
    
    # Verify result
    assert result == "# Invoice\nInvoice Number: 123\nAmount: 1000 DKK"
    assert "Invoice Number: 123" in result
    assert "1000 DKK" in result
    
    # Verify MarkItDown was called correctly
    pdf_service.markitdown.convert.assert_called_once_with("test_invoice.pdf")

def test_convert_to_markdown_empty_result(pdf_service):
    # Mock empty result
    mock_result = MagicMock()
    mock_result.text_content = ""
    
    pdf_service.markitdown.convert.return_value = mock_result
    
    # Test conversion
    result = pdf_service.convert_to_markdown("empty.pdf")
    
    # Verify empty result
    assert result == ""

def test_convert_to_markdown_error(pdf_service):
    # Mock MarkItDown to raise an exception
    pdf_service.markitdown.convert.side_effect = FileNotFoundError("PDF conversion failed")
    
    # Test conversion with error
    result = pdf_service.convert_to_markdown("corrupted.pdf")
    
    # Should return empty string on error
    assert result == ""

def test_convert_to_markdown_danish_content(pdf_service):
    # Test with Danish content (special characters)
    danish_content = """
    Faktura
    Fakturanummer: 112262
    Leverandør: Danfoss A/S
    Beløb: 2.500,75 DKK
    Moms: 625,19 DKK
    """
    
    mock_result = MagicMock()
    mock_result.text_content = danish_content
    
    pdf_service.markitdown.convert.return_value = mock_result
    
    # Test conversion
    result = pdf_service.convert_to_markdown("danish_invoice.pdf")
    
    # Verify Danish content is preserved
    assert "Faktura" in result
    assert "112262" in result
    assert "Danfoss A/S" in result
    assert "2.500,75 DKK" in result

def test_pdf_service_initialization():
    # Test that PDFService initializes correctly with mocked MarkItDown
    with patch('services.pdf_service.MarkItDown') as mock_markitdown:
        mock_instance = MagicMock()
        mock_markitdown.return_value = mock_instance
        
        service = PDFService(enable_plugins=False)
        assert service.markitdown is not None
        
        service_with_plugins = PDFService(enable_plugins=True)
        assert service_with_plugins.markitdown is not None
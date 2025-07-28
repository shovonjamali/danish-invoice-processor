import pytest
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
from services.local_pdf_service import LocalPDFService

@pytest.fixture
def local_pdf_service():
    with patch('services.local_pdf_service.PDFService'), \
         patch('services.local_pdf_service.InvoiceService'):
        return LocalPDFService()

@patch('services.local_pdf_service.LOCAL_PDF_DIR', '/fake/local')
def test_get_pdf_files_success(local_pdf_service):
    # Create proper mock Path objects
    mock_file1 = MagicMock(spec=Path)
    mock_file1.is_file.return_value = True
    mock_file1.__str__.return_value = "/fake/local/invoice1.pdf"
    
    mock_file2 = MagicMock(spec=Path)
    mock_file2.is_file.return_value = True
    mock_file2.__str__.return_value = "/fake/local/invoice2.pdf"
    
    mock_file3 = MagicMock(spec=Path)
    mock_file3.is_file.return_value = False
    mock_file3.__str__.return_value = "/fake/local/not_a_file"
    
    mock_files = [mock_file1, mock_file2, mock_file3]
    
    with patch('pathlib.Path.exists', return_value=True), \
         patch('pathlib.Path.glob', return_value=mock_files):
        
        result = local_pdf_service.get_pdf_files()
        
        # Should return only actual files, sorted
        assert len(result) == 2
        assert "/fake/local/invoice1.pdf" in result
        assert "/fake/local/invoice2.pdf" in result

@patch('services.local_pdf_service.LOCAL_PDF_DIR', '/nonexistent')
def test_get_pdf_files_directory_not_exists(local_pdf_service):
    with patch('pathlib.Path.exists', return_value=False):
        result = local_pdf_service.get_pdf_files()
        assert result == []

def test_get_pdf_files_empty_directory(local_pdf_service):
    with patch('pathlib.Path.exists', return_value=True), \
         patch('pathlib.Path.glob', return_value=[]):
        
        result = local_pdf_service.get_pdf_files()
        assert result == []

def test_process_single_pdf_success(local_pdf_service):
    # Mock successful processing
    local_pdf_service.pdf_service.convert_to_markdown.return_value = "Markdown content"
    local_pdf_service.invoice_service.generate_invoice.return_value = (
        "/output/invoice.xml", 
        {"invoice_number": "123"}, 
        {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    )
    
    with patch.object(local_pdf_service, 'move_to_processed', return_value="/processed/file.pdf"):
        result = local_pdf_service.process_single_pdf("/local/test.pdf")
        
        success, message, token_usage = result
        assert success is True
        assert "Successfully processed" in message
        assert token_usage["total_tokens"] == 150

def test_process_single_pdf_markdown_failure(local_pdf_service):
    # Mock markdown conversion failure
    local_pdf_service.pdf_service.convert_to_markdown.return_value = ""
    
    result = local_pdf_service.process_single_pdf("/local/test.pdf")
    
    success, message, token_usage = result
    assert success is False
    assert "Failed to extract content" in message
    assert token_usage == {}

def test_process_single_pdf_invoice_generation_failure(local_pdf_service):
    # Mock successful markdown but failed invoice generation
    local_pdf_service.pdf_service.convert_to_markdown.return_value = "Content"
    local_pdf_service.invoice_service.generate_invoice.return_value = (None, None, {})
    
    result = local_pdf_service.process_single_pdf("/local/test.pdf")
    
    success, message, token_usage = result
    assert success is False
    assert "Failed to generate invoice" in message

@patch('shutil.move')
@patch('os.path.splitext')
@patch('os.path.basename')
def test_move_to_processed(mock_basename, mock_splitext, mock_move, local_pdf_service):
    # Mock file operations
    mock_basename.return_value = "test.pdf"
    mock_splitext.return_value = ("test", ".pdf")
    
    with patch('services.local_pdf_service.datetime') as mock_datetime:
        mock_datetime.now.return_value.strftime.return_value = "20241201_123000"
        
        with patch('os.path.join', return_value="/processed/test_20241201_123000.pdf"):
            result = local_pdf_service.move_to_processed("/local/test.pdf")
            
            assert result == "/processed/test_20241201_123000.pdf"
            mock_move.assert_called_once()

def test_process_all_pdfs_success(local_pdf_service):
    # Mock multiple PDFs
    with patch.object(local_pdf_service, 'get_pdf_files', return_value=[
        "/local/invoice1.pdf", 
        "/local/invoice2.pdf"
    ]):
        with patch.object(local_pdf_service, 'process_single_pdf') as mock_process:
            # Mock successful processing for both files
            mock_process.side_effect = [
                (True, "Success 1", {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}),
                (True, "Success 2", {"prompt_tokens": 120, "completion_tokens": 60, "total_tokens": 180})
            ]
            
            result = local_pdf_service.process_all_pdfs()
            
            assert result["total_files"] == 2
            assert result["successful"] == 2
            assert result["failed"] == 0
            assert result["total_token_usage"]["total_tokens"] == 330  # 150 + 180

def test_process_all_pdfs_mixed_results(local_pdf_service):
    # Mock mixed success/failure
    with patch.object(local_pdf_service, 'get_pdf_files', return_value=[
        "/local/good.pdf", 
        "/local/bad.pdf"
    ]):
        with patch.object(local_pdf_service, 'process_single_pdf') as mock_process:
            mock_process.side_effect = [
                (True, "Success", {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}),
                (False, "Failed", {"prompt_tokens": 80, "completion_tokens": 0, "total_tokens": 80})
            ]
            
            result = local_pdf_service.process_all_pdfs()
            
            assert result["total_files"] == 2
            assert result["successful"] == 1
            assert result["failed"] == 1
            assert result["total_token_usage"]["total_tokens"] == 230

def test_process_all_pdfs_no_files(local_pdf_service):
    # Test when no PDF files are found
    with patch.object(local_pdf_service, 'get_pdf_files', return_value=[]):
        result = local_pdf_service.process_all_pdfs()
        
        assert result["total_files"] == 0
        assert result["successful"] == 0
        assert result["failed"] == 0
        assert result["total_token_usage"]["total_tokens"] == 0

@patch('builtins.print')  # Mock print to avoid output during tests
def test_process_all_pdfs_output_format(mock_print, local_pdf_service):
    # Test that the output format includes print statements
    with patch.object(local_pdf_service, 'get_pdf_files', return_value=["/local/test.pdf"]):
        with patch.object(local_pdf_service, 'process_single_pdf', return_value=(True, "Success", {})):
            
            local_pdf_service.process_all_pdfs()
            
            # Verify that success message was printed
            mock_print.assert_called()
            calls = [str(call) for call in mock_print.call_args_list]
            assert any("✓" in call for call in calls)  # Success indicator
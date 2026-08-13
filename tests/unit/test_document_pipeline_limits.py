from services.documents.config import DocumentPipelineConfig


def test_default_document_limit_accepts_large_official_tender_pdfs() -> None:
    config = DocumentPipelineConfig()

    assert config.max_file_size_bytes == 100 * 1024 * 1024
    assert config.max_page_pixels == 40_000_000
    assert config.max_pages == 200

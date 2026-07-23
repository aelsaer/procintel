"""Text-layer detection/extraction and rasterization against two small
hand-built PDF fixtures (see tests/fixtures/documents/): one with a real
text operator (`text_layer_sample.pdf`) and one that's image-only, a JPEG
embedded via DCTDecode with no text operators at all
(`scanned_sample.pdf`) — the shape a real scanned tender document takes.
No live network/DB needed; this only exercises pypdfium2 against local
bytes."""

from pathlib import Path

import pytest

from services.documents.config import DocumentPipelineConfig
from services.documents.pdf_text import (
    PdfPageLimitExceededError,
    extract_text_layer,
    open_pdf,
    page_count,
    rasterize_page,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "documents"
TEXT_LAYER_PDF = (FIXTURES / "text_layer_sample.pdf").read_bytes()
SCANNED_PDF = (FIXTURES / "scanned_sample.pdf").read_bytes()


def test_text_layer_pdf_has_usable_text():
    document = open_pdf(TEXT_LAYER_PDF)
    pages = extract_text_layer(document, config=DocumentPipelineConfig())
    assert len(pages) == 1
    assert pages[0].has_usable_text_layer is True
    assert "HELLO PROCINTEL DOCUMENT PIPELINE TEST 999" in pages[0].text_layer


def test_scanned_pdf_has_no_usable_text_layer():
    document = open_pdf(SCANNED_PDF)
    pages = extract_text_layer(document, config=DocumentPipelineConfig())
    assert len(pages) == 1
    assert pages[0].has_usable_text_layer is False
    assert pages[0].text_layer.strip() == ""


def test_page_count_enforces_max_pages():
    document = open_pdf(TEXT_LAYER_PDF)
    with pytest.raises(PdfPageLimitExceededError):
        page_count(document, config=DocumentPipelineConfig(max_pages=0))


def test_rasterize_page_returns_a_pil_image_at_configured_scale():
    document = open_pdf(SCANNED_PDF)
    image = rasterize_page(document, page_number=1, config=DocumentPipelineConfig(ocr_render_scale=2.0))
    # source MediaBox is 600x200 -> scale 2.0 -> 1200x400
    assert image.size == (1200, 400)


def test_rasterize_page_rejects_a_page_exceeding_the_pixel_guard():
    from services.documents.pdf_text import PdfPageTooLargeError

    document = open_pdf(SCANNED_PDF)
    with pytest.raises(PdfPageTooLargeError):
        rasterize_page(document, page_number=1, config=DocumentPipelineConfig(max_page_pixels=100))

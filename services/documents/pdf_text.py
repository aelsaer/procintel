"""PDF text-layer detection/extraction and page rasterization
(§23.1: "text-layer detection", "text extraction", the rasterization step
feeding OCR "only when required").

`pypdfium2` is the only PDF-handling dependency in this pipeline — no
existing code anywhere in the wider tree does PDF rasterization (the
receipt-processing projects under `/home/projects/llmdi` all start from
already-rasterized images), so this is new, not ported from elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass

import pypdfium2 as pdfium

from .config import DocumentPipelineConfig


class PdfPageLimitExceededError(Exception):
    def __init__(self, page_count: int, max_pages: int) -> None:
        super().__init__(f"PDF has {page_count} pages, exceeding the {max_pages}-page limit")
        self.page_count = page_count
        self.max_pages = max_pages


class PdfPageTooLargeError(Exception):
    """Decompression-bomb guard (§23.2): a page whose rendered pixel count
    would exceed the configured limit is refused rather than rasterized."""

    def __init__(self, page_number: int, pixels: int, max_pixels: int) -> None:
        super().__init__(f"page {page_number} would render to {pixels} pixels, exceeding the {max_pixels}-pixel limit")
        self.page_number = page_number
        self.pixels = pixels
        self.max_pixels = max_pixels


@dataclass(frozen=True)
class PdfPageContent:
    page_number: int  # 1-indexed
    text_layer: str
    has_usable_text_layer: bool


def open_pdf(payload: bytes) -> pdfium.PdfDocument:
    return pdfium.PdfDocument(payload)


def page_count(document: pdfium.PdfDocument, *, config: DocumentPipelineConfig) -> int:
    count = len(document)
    if count > config.max_pages:
        raise PdfPageLimitExceededError(count, config.max_pages)
    return count


def extract_text_layer(document: pdfium.PdfDocument, *, config: DocumentPipelineConfig) -> list[PdfPageContent]:
    """One entry per page, text-layer only — never touches OCR. Callers
    decide per-page whether `has_usable_text_layer` is good enough or the
    page needs `rasterize_page` + OCR instead."""
    pages: list[PdfPageContent] = []
    count = page_count(document, config=config)
    for index in range(count):
        page = document[index]
        try:
            text_page = page.get_textpage()
            try:
                text = text_page.get_text_range()
            finally:
                text_page.close()
        finally:
            page.close()
        stripped = text.strip()
        pages.append(
            PdfPageContent(
                page_number=index + 1,
                text_layer=text,
                has_usable_text_layer=len(stripped) >= config.min_text_layer_chars_per_page,
            )
        )
    return pages


def rasterize_page(document: pdfium.PdfDocument, *, page_number: int, config: DocumentPipelineConfig):
    """Renders one 1-indexed page to a Pillow image for OCR. Raises
    `PdfPageTooLargeError` before rendering if the target pixel count would
    exceed `config.max_page_pixels` — the decompression-bomb guard applies
    to the *rendered* size, since that's what actually consumes memory,
    not the PDF's nominal page size."""
    page = document[page_number - 1]
    try:
        width_pt, height_pt = page.get_size()
        rendered_width = int(width_pt * config.ocr_render_scale)
        rendered_height = int(height_pt * config.ocr_render_scale)
        pixels = rendered_width * rendered_height
        if pixels > config.max_page_pixels:
            raise PdfPageTooLargeError(page_number, pixels, config.max_page_pixels)
        bitmap = page.render(scale=config.ocr_render_scale)
        try:
            return bitmap.to_pil()
        finally:
            bitmap.close()
    finally:
        page.close()

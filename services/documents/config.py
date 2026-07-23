"""Document pipeline configuration (spec §23.2's security guards).

No `base_url`/API key here — unlike the ingestion connectors, this
pipeline's inputs are document URLs already discovered by other connectors
(Διαύγεια attachments, ΚΗΜΔΗΣ tender documents, ...), not a single source
API. Every numeric guard below exists to satisfy a specific §23.2 bullet;
none are arbitrary.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentPipelineConfig:
    max_file_size_bytes: int = 50 * 1024 * 1024  # §23.2 "μέγιστο μέγεθος αρχείου"
    download_timeout_seconds: float = 30.0  # §23.2 "timeout"
    max_page_pixels: int = 40_000_000  # §23.2 decompression-bomb guard for rasterized pages (~6300x6300)
    max_pages: int = 200  # refuses to OCR unbounded page counts from a hostile/corrupt PDF
    ocr_lang: str = "ell+eng"  # Greek primary, English fallback (tesseract multi-lang string)
    ocr_psm: int = 6  # assume a single uniform block of text — reasonable default for scanned tender docs
    ocr_oem: int = 1  # LSTM engine only
    ocr_timeout_seconds: float = 60.0
    ocr_render_scale: float = 2.0  # pypdfium2 render scale (~144 DPI at scale 2.0), improves OCR accuracy over 1.0
    min_text_layer_chars_per_page: int = 20  # below this, treat the page as having no usable text layer -> OCR
    allowed_mime_types: frozenset[str] = frozenset({"application/pdf"})
    # Extraction targets beyond ΑΔΑ/ΑΔΑΜ/ΑΦΜ/CPV/amounts/dates are opt-in
    # per source-record, not a pipeline-wide switch — IBAN specifically
    # (§23.3 "μόνο όπου επιτρέπεται") defaults to off.
    extract_iban: bool = False

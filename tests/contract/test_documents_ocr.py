"""Tesseract-subprocess OCR wrapper. Runs the real `tesseract` CLI binary
(confirmed present at /usr/bin/tesseract, v5.3.4, in the environment this
was built in — see module docstring in services/documents/ocr.py) against
the `eng` tessdata already installed system-wide; skipped automatically if
`tesseract` isn't on PATH at all, so this file doesn't hard-fail in an
environment without it.

Greek (`ell`) OCR now works out of the box — `ocr.py` bundles
`ell.traineddata`/`eng.traineddata`/`configs/` under
`services/documents/tessdata/` and points `TESSDATA_PREFIX` there
automatically. `test_run_ocr_reads_greek_text` renders real Greek text
with a DejaVu Sans font (the one confirmed present here, at
`/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf` — PIL's default bitmap
font has no Greek glyphs) and is skipped, not hard-failed, if that
specific font isn't found elsewhere."""

import asyncio
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from services.documents.config import DocumentPipelineConfig
from services.documents.ocr import OcrTimeoutError, run_ocr

pytestmark = pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract CLI not on PATH")

_GREEK_CAPABLE_FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


def _text_image(text: str) -> Image.Image:
    image = Image.new("RGB", (600, 150), "white")
    draw = ImageDraw.Draw(image)
    draw.text((20, 50), text, fill="black")
    return image


def _greek_text_image(text: str) -> Image.Image:
    image = Image.new("RGB", (700, 150), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(_GREEK_CAPABLE_FONT), 32)
    draw.text((20, 50), text, fill="black", font=font)
    return image


def test_run_ocr_reads_rendered_text():
    image = _text_image("HELLO PROCINTEL 12345")
    result = asyncio.run(run_ocr(image, config=DocumentPipelineConfig(ocr_lang="eng")))
    assert "HELLO" in result.text
    assert "PROCINTEL" in result.text
    assert result.mean_confidence is not None
    assert result.mean_confidence > 50


def test_run_ocr_blank_image_returns_empty_text_and_no_confidence():
    image = Image.new("RGB", (300, 100), "white")
    result = asyncio.run(run_ocr(image, config=DocumentPipelineConfig(ocr_lang="eng")))
    assert result.text == ""
    assert result.mean_confidence is None


@pytest.mark.skipif(not _GREEK_CAPABLE_FONT.exists(), reason=f"{_GREEK_CAPABLE_FONT} not found")
def test_run_ocr_reads_greek_text():
    image = _greek_text_image("ΔΗΜΟΣ ΑΘΗΝΑΙΩΝ 12345")
    result = asyncio.run(run_ocr(image, config=DocumentPipelineConfig(ocr_lang="ell+eng")))
    assert "ΔΗΜΟΣ" in result.text
    assert "ΑΘΗΝΑΙΩΝ" in result.text
    assert result.mean_confidence is not None
    assert result.mean_confidence > 50


def test_run_ocr_raises_on_timeout(monkeypatch):
    import subprocess

    def _fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="tesseract", timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr(subprocess, "run", _fake_run)
    image = _text_image("irrelevant")
    with pytest.raises(OcrTimeoutError):
        asyncio.run(run_ocr(image, config=DocumentPipelineConfig(ocr_lang="eng", ocr_timeout_seconds=1)))

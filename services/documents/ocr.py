"""Tesseract OCR via subprocess CLI call — not the `pytesseract`/`tesserocr`
Python bindings.

Ported (structure only, not code) from the pattern found in
`receiptx_v3_bundle/receiptx_v3/ocr.py:46-101` in the wider
`/home/projects/llmdi` tree, per that project's own research: the system
`tesseract` CLI binary works correctly here (v5.3.4), while both PaddleOCR
and Surya OCR are currently broken in the separate `llmdi` conda
environment (numpy 2.x / opencv ABI conflicts) — and neither would be
importable from this Python 3.11+ venv anyway, since they live in an
unrelated Python 3.10 environment. Shelling out to the CLI sidesteps both
problems entirely.

Greek OCR requires the `ell` tessdata file. The system package
(`tesseract-ocr-eng`) does **not** include it — rather than requiring an
`apt install`/root access this module doesn't control, `ell.traineddata`
(+ `eng.traineddata`, so the bundled directory is self-sufficient for the
default `ocr_lang="ell+eng"`) is bundled directly in this repo under
`services/documents/tessdata/` (both Apache-2.0 licensed — see
`tessdata/LICENSE` — from the official tesseract-ocr/tessdata project).
`_tesseract_env()` points `TESSDATA_PREFIX` at that bundled directory
automatically unless the environment already sets `TESSDATA_PREFIX`
explicitly (an operator's own, presumably more complete, tessdata install
always wins).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .config import DocumentPipelineConfig


class OcrTimeoutError(Exception):
    def __init__(self, timeout_seconds: float) -> None:
        super().__init__(f"tesseract did not finish within {timeout_seconds}s")


_BUNDLED_TESSDATA_DIR = Path(__file__).parent / "tessdata"


def _tesseract_env() -> dict[str, str] | None:
    """Returns an environment dict with `TESSDATA_PREFIX` pointed at the
    bundled `ell`+`eng` tessdata directory, or `None` to inherit the
    current environment unchanged (`subprocess.run(..., env=None)` means
    "use this process's environment as-is") — either because
    `TESSDATA_PREFIX` is already set (an explicit operator choice always
    wins) or the bundled directory isn't present for some reason."""
    if "TESSDATA_PREFIX" in os.environ:
        return None
    if not _BUNDLED_TESSDATA_DIR.is_dir():
        return None
    env = os.environ.copy()
    env["TESSDATA_PREFIX"] = str(_BUNDLED_TESSDATA_DIR)
    return env


@dataclass(frozen=True)
class OcrResult:
    text: str
    mean_confidence: float | None  # None if tesseract produced no confident words at all


def _resolve_tesseract_executable() -> str:
    exe = shutil.which("tesseract")
    if exe:
        return exe
    py_bin_dir = os.path.dirname(sys.executable)
    candidate = os.path.join(py_bin_dir, "tesseract")
    if os.path.exists(candidate):
        return candidate
    return "tesseract"


def _run_tesseract_sync(image: Image.Image, *, config: DocumentPipelineConfig) -> OcrResult:
    with tempfile.TemporaryDirectory() as tmpdir:
        in_path = Path(tmpdir) / "in.png"
        out_base = Path(tmpdir) / "out"
        image.save(in_path, format="PNG")

        cmd = [
            _resolve_tesseract_executable(),
            str(in_path),
            str(out_base),
            "-l",
            config.ocr_lang,
            "--oem",
            str(config.ocr_oem),
            "--psm",
            str(config.ocr_psm),
            "tsv",
        ]
        try:
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=config.ocr_timeout_seconds,
                env=_tesseract_env(),
            )
        except subprocess.TimeoutExpired:
            raise OcrTimeoutError(config.ocr_timeout_seconds) from None

        out_tsv = out_base.with_suffix(".tsv")
        if not out_tsv.exists():
            return OcrResult(text="", mean_confidence=None)

        words: list[str] = []
        confidences: list[float] = []
        with open(out_tsv, encoding="utf-8", errors="ignore") as handle:
            import csv

            for row in csv.DictReader(handle, delimiter="\t"):
                text = (row.get("text") or "").strip()
                if not text:
                    continue
                words.append(text)
                try:
                    conf = float(row.get("conf", "-1"))
                except ValueError:
                    conf = -1.0
                if conf >= 0:
                    confidences.append(conf)

        mean_confidence = sum(confidences) / len(confidences) if confidences else None
        return OcrResult(text=" ".join(words), mean_confidence=mean_confidence)


async def run_ocr(image: Image.Image, *, config: DocumentPipelineConfig) -> OcrResult:
    return await asyncio.to_thread(_run_tesseract_sync, image, config=config)

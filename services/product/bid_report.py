"""Deterministic BID/NO-BID recommendation and portable PDF rendering."""

from __future__ import annotations

import os
import unicodedata
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable


def derive_recommendation(
    *,
    opportunity_score: float,
    data_confidence: float,
    mandatory_blockers: int,
    deadline_passed: bool,
) -> tuple[str, float, list[str]]:
    confidence = round(
        max(0.0, min(100.0, opportunity_score * 0.65 + data_confidence * 0.35)),
        1,
    )
    reasons: list[str] = []
    if deadline_passed:
        return "NO_BID", max(confidence, 95.0), ["Η προθεσμία υποβολής έχει παρέλθει."]
    if mandatory_blockers:
        reasons.append(
            f"{mandatory_blockers} υποχρεωτικές απαιτήσεις δεν έχουν καλυφθεί."
        )
    if opportunity_score < 40:
        reasons.append("Η συνολική επιχειρηματική καταλληλότητα είναι χαμηλή.")
    if data_confidence < 55:
        reasons.append("Η κάλυψη δεδομένων δεν επαρκεί για οριστική απόφαση.")

    if mandatory_blockers >= 2 or opportunity_score < 35:
        recommendation = "NO_BID"
    elif mandatory_blockers or opportunity_score < 68 or data_confidence < 60:
        recommendation = "CONDITIONAL"
    else:
        recommendation = "BID"

    if not reasons:
        reasons.append("Η καταλληλότητα και η κάλυψη δεδομένων υπερβαίνουν τα όρια.")
    return recommendation, confidence, reasons


def recommended_actions(
    *,
    recommendation: str,
    missing_requirements: Iterable[dict[str, Any]],
    missing_certificates: Iterable[dict[str, Any]],
    deadline: datetime | None,
) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for requirement in list(missing_requirements)[:5]:
        actions.append(
            {
                "type": "REQUIREMENT",
                "priority": "URGENT" if requirement.get("mandatory") else "NORMAL",
                "label": f"Κάλυψη απαίτησης: {requirement.get('title', 'χωρίς τίτλο')}",
            }
        )
    for certificate in list(missing_certificates)[:4]:
        actions.append(
            {
                "type": "CERTIFICATE",
                "priority": "HIGH",
                "label": f"Εξασφάλιση πιστοποιητικού: {certificate.get('title', 'χωρίς τίτλο')}",
            }
        )
    if deadline is not None:
        actions.append(
            {
                "type": "DEADLINE",
                "priority": "HIGH",
                "label": f"Επιβεβαίωση πλάνου υποβολής έως {deadline:%d/%m/%Y %H:%M}",
            }
        )
    if recommendation == "BID":
        actions.append(
            {
                "type": "DECISION",
                "priority": "NORMAL",
                "label": "Έγκριση BID και ανάθεση ιδιοκτητών proposal.",
            }
        )
    elif recommendation == "CONDITIONAL":
        actions.append(
            {
                "type": "DECISION",
                "priority": "HIGH",
                "label": "Επανεξέταση απόφασης μετά την κάλυψη των blockers.",
            }
        )
    else:
        actions.append(
            {
                "type": "DECISION",
                "priority": "NORMAL",
                "label": "Καταγραφή NO-BID rationale για μελλοντική μάθηση.",
            }
        )
    return actions


def _ascii(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    return text.encode("ascii", "replace").decode("ascii")


def _basic_pdf(lines: Iterable[str]) -> bytes:
    """Dependency-free valid PDF fallback used in minimal worker images."""
    content_lines = ["BT", "/F1 10 Tf", "42 800 Td", "13 TL"]
    for line in lines:
        escaped = _ascii(line).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content_lines.append(f"({escaped[:150]}) Tj")
        content_lines.append("T*")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = BytesIO()
    output.write(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(output.tell())
        output.write(f"{index} 0 obj\n".encode())
        output.write(obj)
        output.write(b"\nendobj\n")
    xref = output.tell()
    output.write(f"xref\n0 {len(objects) + 1}\n".encode())
    output.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.write(f"{offset:010d} 00000 n \n".encode())
    output.write(
        (
            f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF"
        ).encode()
    )
    return output.getvalue()


def _report_lines(report: dict[str, Any]) -> list[str]:
    lines = [
        "PROCINTEL - BID / NO-BID REPORT",
        str(report.get("title") or "Procurement opportunity"),
        f"Recommendation: {report.get('recommendation', '-')}",
        f"Confidence: {report.get('confidence', 0)}%",
        f"Buyer: {report.get('buyer_name') or '-'}",
        f"Budget: {report.get('budget') or '-'} EUR",
        f"Deadline: {report.get('deadline') or '-'}",
        f"Geography: {', '.join(report.get('geography') or []) or '-'}",
        "",
        "DECISION RATIONALE",
    ]
    lines.extend(f"- {item}" for item in report.get("recommendation_reasons") or [])
    for heading, key in (
        ("RISKS AND BLOCKERS", "risks"),
        ("MANDATORY REQUIREMENTS", "mandatory_requirements"),
        ("MISSING CERTIFICATES", "missing_certificates"),
        ("INCUMBENT AND COMPETITORS", "competitors"),
        ("RECOMMENDED NEXT ACTIONS", "next_actions"),
        ("OFFICIAL EVIDENCE", "evidence"),
    ):
        lines.extend(["", heading])
        for item in report.get(key) or []:
            if isinstance(item, dict):
                label = (
                    item.get("label")
                    or item.get("title")
                    or item.get("name")
                    or item.get("source_identifier")
                    or str(item)
                )
            else:
                label = item
            lines.append(f"- {label}")
    return lines


def render_bid_report_pdf(report: dict[str, Any]) -> bytes:
    lines = _report_lines(report)
    try:
        from fpdf import FPDF  # type: ignore[import-not-found]

        font_path = os.environ.get("PROCINTEL_PDF_FONT")
        candidates = [
            font_path,
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        ]
        usable_font = next(
            (candidate for candidate in candidates if candidate and Path(candidate).exists()),
            None,
        )
        if usable_font is None:
            return _basic_pdf(lines)

        pdf = FPDF(format="A4")
        pdf.set_auto_page_break(auto=True, margin=16)
        pdf.add_font("Procintel", fname=usable_font)
        pdf.add_page()
        pdf.set_fill_color(21, 38, 48)
        pdf.rect(0, 0, 210, 31, style="F")
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Procintel", size=16)
        pdf.set_xy(15, 10)
        pdf.cell(0, 8, "Procintel  |  BID / NO-BID")
        pdf.set_xy(15, 36)
        pdf.set_text_color(30, 45, 53)
        pdf.set_font("Procintel", size=13)
        pdf.multi_cell(180, 7, str(report.get("title") or "Procurement opportunity"))
        pdf.ln(2)
        pdf.set_font("Procintel", size=9)
        for line in lines[2:]:
            if not line:
                pdf.ln(2)
                continue
            if line.isupper():
                pdf.set_text_color(53, 111, 94)
                pdf.set_font("Procintel", size=10)
                pdf.multi_cell(180, 6, line)
                pdf.set_text_color(55, 66, 73)
                pdf.set_font("Procintel", size=9)
            else:
                pdf.multi_cell(180, 5, line)
        rendered = pdf.output()
        return bytes(rendered)
    except Exception:
        return _basic_pdf(lines)

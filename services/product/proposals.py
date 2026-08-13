"""Evidence-grounded proposal drafting helpers and dependency-free DOCX output."""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

_TOKEN_RE = re.compile(r"[^\W_]{3,}", re.UNICODE)
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


@dataclass(frozen=True)
class ReusableContent:
    id: str
    title: str
    body: str
    tags: tuple[str, ...] = ()


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_RE.findall(value)}


def select_reusable_content(
    requirement_text: str,
    contents: list[ReusableContent],
    *,
    limit: int = 3,
) -> list[ReusableContent]:
    requirement_tokens = _tokens(requirement_text)
    ranked: list[tuple[float, ReusableContent]] = []
    for content in contents:
        content_tokens = _tokens(" ".join((content.title, content.body, *content.tags)))
        overlap = len(requirement_tokens & content_tokens)
        score = overlap / max(1, len(requirement_tokens))
        if score > 0:
            ranked.append((score, content))
    ranked.sort(key=lambda item: (item[0], item[1].title), reverse=True)
    return [item[1] for item in ranked[:limit]]


def deterministic_first_draft(
    *,
    requirement_title: str,
    requirement_description: str | None,
    evidence_excerpt: str | None,
    reusable_content: list[ReusableContent],
) -> str:
    """Provide a useful, explicitly incomplete draft when no LLM is configured."""
    paragraphs = [
        f"ΑΠΑΙΤΗΣΗ: {requirement_title}",
        "",
        (
            "ΠΡΩΤΟ ΣΧΕΔΙΟ: Η ομάδα προσφοράς θα τεκμηριώσει τη συμμόρφωση με την "
            "παραπάνω απαίτηση βάσει των επίσημων όρων και των εγκεκριμένων "
            "εταιρικών στοιχείων."
        ),
    ]
    if requirement_description:
        paragraphs.extend(("", f"Περιγραφή απαίτησης: {requirement_description}"))
    if reusable_content:
        paragraphs.extend(("", "Εγκεκριμένο επαναχρησιμοποιήσιμο υλικό:"))
        paragraphs.extend(f"- {item.title}: {item.body}" for item in reusable_content)
    if evidence_excerpt:
        paragraphs.extend(("", f"Επίσημη τεκμηρίωση: «{evidence_excerpt}»"))
    paragraphs.extend(
        (
            "",
            "TODO ΠΡΙΝ ΤΗΝ ΕΓΚΡΙΣΗ:",
            "- Επιβεβαίωση κάθε πραγματικού ισχυρισμού από αρμόδιο μέλος της ομάδας.",
            "- Προσθήκη μετρήσιμων στοιχείων, παραδοτέων και υπευθύνων.",
            "- Νομικός και τεχνικός έλεγχος έναντι του επίσημου τεύχους.",
        )
    )
    return "\n".join(paragraphs)


def proposal_prompt(
    *,
    requirement_title: str,
    requirement_description: str | None,
    evidence_excerpt: str | None,
    reusable_content: list[ReusableContent],
    language: str,
) -> str:
    reusable = "\n\n".join(f"{item.title}\n{item.body}" for item in reusable_content) or "None"
    return (
        f"Draft language: {language}\n"
        f"Requirement: {requirement_title}\n"
        f"Description: {requirement_description or 'Not supplied'}\n"
        f"Official evidence excerpt: {evidence_excerpt or 'Not supplied'}\n"
        f"Approved reusable company content:\n{reusable}\n\n"
        "Write a concise response draft. Use only the supplied evidence and approved "
        "company content for factual claims. Mark every missing fact as [TODO]. "
        "Do not claim certification, experience, staffing, or compliance unless it "
        "appears above. Preserve a clear response structure."
    )


def _paragraph(parent: ET.Element, text: str, *, style: str | None = None) -> None:
    paragraph = ET.SubElement(parent, f"{{{_W}}}p")
    if style:
        properties = ET.SubElement(paragraph, f"{{{_W}}}pPr")
        ET.SubElement(properties, f"{{{_W}}}pStyle", {f"{{{_W}}}val": style})
    run = ET.SubElement(paragraph, f"{{{_W}}}r")
    node = ET.SubElement(run, f"{{{_W}}}t")
    if text.startswith(" ") or text.endswith(" "):
        node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    node.text = text


def render_proposal_docx(
    *,
    title: str,
    sections: list[dict],
    generated_at: datetime | None = None,
) -> bytes:
    """Render a standards-compliant minimal DOCX without native dependencies."""
    generated_at = generated_at or datetime.now(timezone.utc)
    document = ET.Element(f"{{{_W}}}document", {"xmlns:r": _R})
    body = ET.SubElement(document, f"{{{_W}}}body")
    _paragraph(body, title, style="Title")
    _paragraph(body, f"Generated {generated_at.isoformat()}")
    _paragraph(body, "Working draft. Approval is required before submission.")
    for section in sections:
        _paragraph(body, str(section.get("title") or "Untitled section"), style="Heading1")
        _paragraph(
            body,
            f"Status: {section.get('status', 'DRAFT')} · Version {section.get('current_version', 1)}",
        )
        for line in str(section.get("body") or "").splitlines():
            _paragraph(body, line)
        citations = section.get("citations") or []
        if citations:
            _paragraph(body, "Official evidence", style="Heading2")
            for index, citation in enumerate(citations, start=1):
                label = citation.get("document_title") or citation.get("source_url") or "Official document"
                page = f", page {citation['page']}" if citation.get("page") else ""
                _paragraph(body, f"[{index}] {label}{page}: {citation.get('excerpt') or ''}")
    section_properties = ET.SubElement(body, f"{{{_W}}}sectPr")
    ET.SubElement(
        section_properties,
        f"{{{_W}}}pgSz",
        {f"{{{_W}}}w": "11906", f"{{{_W}}}h": "16838"},
    )

    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""
    relationships = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
    document_relationships = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>"""
    styles = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:styles xmlns:w="{_W}">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
  <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:rPr><w:b/><w:sz w:val="36"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:rPr><w:b/><w:sz w:val="28"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style>
</w:styles>"""

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/_rels/document.xml.rels", document_relationships)
        archive.writestr("word/styles.xml", styles)
        archive.writestr(
            "word/document.xml",
            ET.tostring(document, encoding="utf-8", xml_declaration=True),
        )
    return output.getvalue()

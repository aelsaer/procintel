import io
import zipfile

from services.product.proposals import (
    ReusableContent,
    deterministic_first_draft,
    render_proposal_docx,
    select_reusable_content,
)


def test_reusable_content_is_ranked_by_requirement_overlap():
    contents = [
        ReusableContent("1", "GIS delivery", "We deliver GIS systems.", ("GIS",)),
        ReusableContent("2", "Catering", "Meal delivery.", ("food",)),
    ]
    selected = select_reusable_content("Technical requirement for a GIS system", contents)
    assert [item.id for item in selected] == ["1"]


def test_fallback_draft_is_explicit_about_missing_validation():
    draft = deterministic_first_draft(
        requirement_title="ISO 27001",
        requirement_description=None,
        evidence_excerpt="The bidder shall document information security controls.",
        reusable_content=[],
    )
    assert "TODO ΠΡΙΝ ΤΗΝ ΕΓΚΡΙΣΗ" in draft
    assert "ISO 27001" in draft
    assert "Official" not in draft


def test_docx_export_is_valid_ooxml_and_contains_evidence():
    payload = render_proposal_docx(
        title="Technical proposal",
        sections=[
            {
                "title": "Requirement A",
                "body": "Response body",
                "status": "IN_REVIEW",
                "current_version": 2,
                "citations": [{"document_title": "Official notice", "page": 4, "excerpt": "Evidence"}],
            }
        ],
    )
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())
        document = archive.read("word/document.xml").decode("utf-8")
    assert "[Content_Types].xml" in names
    assert "Technical proposal" in document
    assert "Official notice" in document

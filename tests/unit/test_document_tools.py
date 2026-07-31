import io
import zipfile

from services.product.document_tools import (
    build_document_archive,
    match_document_phrases,
    render_editable_document_docx,
)


def test_phrase_matching_is_accent_insensitive_and_supports_all_mode():
    pages = [(1, "Απαίτηση για Γεωγραφικό Σύστημα Πληροφοριών"), (2, "Πιστοποίηση ISO 27001")]
    match = match_document_phrases(pages, ["γεωγραφικό σύστημα", "ISO 27001"], match_mode="ALL")
    assert match is not None
    assert match.page_numbers == (1, 2)
    assert match.matched_phrases == ("γεωγραφικό σύστημα", "ISO 27001")
    assert match_document_phrases(pages, ["ανύπαρκτο"], match_mode="ANY") is None


def test_bulk_archive_contains_files_and_machine_readable_manifest():
    payload = build_document_archive(
        [("notice.pdf", b"%PDF"), ("notice.pdf", b"%PDF-second")],
        manifest={"documents": 2},
    )
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert {"notice.pdf", "notice-2.pdf", "manifest.json"} <= set(archive.namelist())


def test_editable_conversion_is_a_valid_docx_with_page_text():
    payload = render_editable_document_docx(
        title="Notice",
        pages=[(1, "Official requirement")],
        source_url="https://example.gov.gr/notice.pdf",
    )
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        document = archive.read("word/document.xml").decode()
    assert "Official requirement" in document
    assert "Formatting may differ" in document

from services.documents.mime import sniff_mime_type


def test_sniffs_pdf_by_magic_bytes():
    assert sniff_mime_type(b"%PDF-1.4\n...") == "application/pdf"


def test_sniffs_png():
    assert sniff_mime_type(b"\x89PNG\r\n\x1a\n...") == "image/png"


def test_sniffs_jpeg():
    assert sniff_mime_type(b"\xff\xd8\xff\xe0...") == "image/jpeg"


def test_unrecognized_bytes_return_none():
    assert sniff_mime_type(b"just some plain text, not a real file") is None


def test_ignores_a_forged_content_type_header_and_looks_at_the_bytes():
    # the whole point of sniffing: an attacker-controlled filename/header
    # claiming .pdf doesn't make arbitrary bytes a PDF
    fake_pdf_bytes = b"<html><body>not actually a pdf</body></html>"
    assert sniff_mime_type(fake_pdf_bytes) != "application/pdf"

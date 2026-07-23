from pathlib import Path

import pytest
from xml.etree.ElementTree import ParseError

from services.ingestion.connectors.ted.normalize import normalize_ted_notice, parse_bulk_xml_package

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "ted" / "bulk_package_sample.xml"


def test_parses_all_notices_from_package():
    xml_bytes = FIXTURE_PATH.read_bytes()
    records = parse_bulk_xml_package(xml_bytes)
    assert len(records) == 2
    assert records[0]["noticeId"] == "2025-TED-BULK-001"
    assert records[1]["noticeId"] == "2025-TED-BULK-002"


def test_notice_fields_extracted_correctly():
    records = parse_bulk_xml_package(FIXTURE_PATH.read_bytes())
    first = records[0]
    assert first["title"] == "Παροχή υπηρεσιών καθαρισμού δημοσίων κτιρίων"
    assert first["buyer"] == {"name": "ΔΗΜΟΣ ΔΟΚΙΜΗΣ", "vatNumber": "094259216", "countryCode": "GR"}
    assert first["supplier"] == {"name": "ΑΛΦΑ ΚΑΘΑΡΙΣΜΟΙ ΙΚΕ", "vatNumber": "090000045", "countryCode": "GR"}
    assert first["cpvCodes"] == ["90911200", "90910000"]
    assert first["estimatedValue"] == "100000.00"
    assert first["awardedValue"] == "124000.00"
    assert first["nutsCodes"] == ["EL301"]
    assert first["publicationDate"] == "2025-01-15"


def test_missing_optional_elements_become_none_or_empty():
    records = parse_bulk_xml_package(FIXTURE_PATH.read_bytes())
    second = records[1]
    assert second["supplier"] is None
    assert second["awardedValue"] is None
    assert second["nutsCodes"] == []


def test_parsed_record_feeds_normalize_ted_notice_unchanged():
    records = parse_bulk_xml_package(FIXTURE_PATH.read_bytes())
    normalized = normalize_ted_notice(records[0], ted_notice_id=records[0]["noticeId"])
    assert normalized.title == "Παροχή υπηρεσιών καθαρισμού δημοσίων κτιρίων"
    assert normalized.buyer.vat == "094259216"
    assert normalized.awarded_value == 124000.00
    assert normalized.publication_date.isoformat() == "2025-01-15"


def test_malformed_xml_raises_parse_error():
    with pytest.raises(ParseError):
        parse_bulk_xml_package(b"<TedExport><Notice><Title>unterminated")

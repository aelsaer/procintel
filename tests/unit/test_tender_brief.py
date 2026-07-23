from services.intelligence.tender_brief import (
    khmdhs_attachment_url,
    links_for_display_identifier,
    official_links,
)


def test_khmdhs_notice_links_are_provider_owned_and_stable():
    official_url, document_url = links_for_display_identifier("ADAM", "26PROC019308569")

    assert official_url == (
        "https://cerpp.eprocurement.gov.gr/upgkimdis/unprotected/"
        "home.xhtml?referenceNumber=26PROC019308569"
    )
    assert document_url == (
        "https://cerpp.eprocurement.gov.gr/khmdhs-opendata/"
        "notice/attachment/26PROC019308569"
    )


def test_khmdhs_resource_is_derived_for_every_adam_family():
    expected = {
        "25REQ000000001": "request",
        "25PROC000000001": "notice",
        "25AWRD000000001": "auction",
        "25SYMV000000001": "contract",
        "25PAY000000001": "payment",
    }
    for adam, resource in expected.items():
        _, document_url = links_for_display_identifier("ADAM", adam)
        assert document_url == khmdhs_attachment_url(resource, adam)


def test_diavgeia_and_ted_links_point_to_official_record_and_pdf():
    assert official_links(
        source_system="DIAVGEIA",
        resource_type="decision",
        identifier_scheme="ADA",
        identifier="6ΑΒΓ-123",
    ) == (
        "https://diavgeia.gov.gr/decision/view/6%CE%91%CE%92%CE%93-123",
        "https://diavgeia.gov.gr/doc/6%CE%91%CE%92%CE%93-123",
    )
    assert official_links(
        source_system="TED",
        resource_type="notice",
        identifier_scheme="TED_NOTICE_ID",
        identifier="123456-2026",
    ) == (
        "https://ted.europa.eu/en/notice/-/detail/123456-2026",
        "https://ted.europa.eu/en/notice/123456-2026/pdf",
    )

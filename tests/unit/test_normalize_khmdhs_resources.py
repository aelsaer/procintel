import json
from decimal import Decimal
from pathlib import Path

import pytest

from services.ingestion.connectors.khmdhs.normalize import normalize_khmdhs_record

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "khmdhs"


def _first_record(filename: str) -> dict:
    body = json.loads((FIXTURES_DIR / filename).read_text(encoding="utf-8"))
    return body["data"][0]


@pytest.mark.parametrize(
    "resource,filename,expected_act_type",
    [
        ("request", "request_sample.json", "REQUEST"),
        ("notice", "notice_sample.json", "NOTICE"),
        ("auction", "auction_sample.json", "AWARD"),
        ("contract", "contract_sample.json", "CONTRACT"),
        ("payment", "payment_sample.json", "PAYMENT"),
    ],
)
def test_each_resource_maps_to_its_act_type(resource, filename, expected_act_type):
    record = _first_record(filename)
    normalized = normalize_khmdhs_record(record, resource=resource)
    assert normalized.act_type == expected_act_type
    assert normalized.buyer is not None
    assert normalized.buyer.afm_checksum_valid is True


def test_auction_resource_captures_awardee_as_contractor():
    record = _first_record("auction_sample.json")
    normalized = normalize_khmdhs_record(record, resource="auction")
    assert normalized.contractor is not None
    assert normalized.contractor.afm_normalized == "090000045"


def test_payment_resource_uses_extra_fallback_date_and_amount_keys():
    record = _first_record("payment_sample.json")
    normalized = normalize_khmdhs_record(record, resource="payment")
    assert normalized.submission_date is not None
    assert normalized.submission_date.isoformat() == "2025-02-01"
    assert normalized.amount_gross == Decimal("9672.00")


def test_unknown_resource_is_rejected():
    with pytest.raises(ValueError):
        normalize_khmdhs_record({"referenceNumber": "X"}, resource="not-a-resource")


def test_duplicate_cpv_codes_are_collapsed_before_canonical_insert():
    normalized = normalize_khmdhs_record(
        {
            "referenceNumber": "26SYMV019542829",
            "objectDetailsList": [
                {"cpvs": ["71356300-1", "71356300-1", "71350000-6"]},
                {"cpvs": ["71356300-1"]},
            ],
        },
        resource="contract",
    )

    assert normalized.cpv_codes == ["71356300-1", "71350000-6"]


def test_notice_preserves_real_provider_summary_fields():
    normalized = normalize_khmdhs_record(
        {
            "referenceNumber": "26PROC019308569",
            "title": "Διακήρυξη εξοπλισμού",
            "publishedDate": "2026-06-24",
            "finalSubmissionDate": "2026-07-10T13:00:00+03:00",
            "typeOfProcedure": "Ανοιχτή διαδικασία",
            "criteriaCode": "Βάσει τιμής",
            "biddingWebsite": "https://example.test/tender",
            "objectDetails": [
                {
                    "shortDescription": "Πομποί και παρελκόμενα",
                    "cpv": [{"code": "38290000"}],
                    "cost": 15700,
                    "quantity": 14,
                    "unit": "τεμάχια",
                }
            ],
        },
        resource="notice",
    )

    assert normalized.publication_date.isoformat() == "2026-06-24"
    assert normalized.submission_deadline.isoformat() == "2026-07-10T13:00:00+03:00"
    assert normalized.procedure_type == "Ανοιχτή διαδικασία"
    assert normalized.source_details["award_criterion"] == "Βάσει τιμής"
    assert normalized.source_details["bidding_website"] == "https://example.test/tender"
    assert normalized.source_details["object_details"][0]["short_description"] == "Πομποί και παρελκόμενα"


def test_live_contract_shape_preserves_all_consortium_members_and_dates():
    normalized = normalize_khmdhs_record(
        {
            "referenceNumber": "26SYMV019498766",
            "contractSignedDate": "2026-01-12",
            "endDate": "2027-01-11",
            "totalCostWithoutVAT": 3000,
            "totalCostWithVAT": 3720,
            "contractingDataDetails": {
                "contractingMembersDataList": [
                    {"name": "Supplier A", "vatNumber": "090000045"},
                    {"name": "Supplier B", "vatNumber": "094019245"},
                ]
            },
            "objectDetailsList": [
                {
                    "city": "ΧΑΙΔΑΡΙ",
                    "postalCode": "12461",
                    "costWithoutVAT": 3000,
                }
            ],
            "decisionRelatedAda": ["ΑΔΑ-1", "ΑΔΑ-2"],
            "diavgeiaADA": "ΑΔΑ-3",
        },
        resource="contract",
    )

    assert normalized.publication_date.isoformat() == "2026-01-12"
    assert normalized.end_date.isoformat() == "2027-01-11"
    assert normalized.vat_amount == Decimal("720")
    assert [party.afm_normalized for party in normalized.contractors] == [
        "090000045",
        "094019245",
    ]
    assert normalized.contractor == normalized.contractors[0]
    assert normalized.related_ada == ["ΑΔΑ-1", "ΑΔΑ-2", "ΑΔΑ-3"]
    assert normalized.source_details["city"] == "ΧΑΙΔΑΡΙ"
    assert normalized.source_details["postal_code"] == "12461"


def test_live_payment_shape_maps_payee_location_and_official_reference():
    normalized = normalize_khmdhs_record(
        {
            "referenceNumber": "26PAY019497778",
            "signedDate": "2026-04-22T00:00:00",
            "totalCostWithoutVAT": 5200,
            "totalCostWithVAT": 6448,
            "paymentRelatedAda": "Ψ123-ABC",
            "objectDetails": [
                {
                    "name": "Payment Supplier",
                    "vatNo": "090000045",
                    "city": "ΔΡΑΜΑ",
                    "postalCode": "66100",
                    "costWithoutVAT": 5200,
                }
            ],
        },
        resource="payment",
    )

    assert normalized.submission_date.isoformat() == "2026-04-22"
    assert normalized.contractor.afm_normalized == "090000045"
    assert normalized.contractors == [normalized.contractor]
    assert normalized.related_ada == ["Ψ123-ABC"]
    assert normalized.source_details["city"] == "ΔΡΑΜΑ"
    assert normalized.source_details["postal_code"] == "66100"

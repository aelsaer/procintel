from datetime import datetime, timezone

from services.product.bid_report import (
    derive_recommendation,
    recommended_actions,
    render_bid_report_pdf,
)


def test_bid_recommendation_requires_high_fit_and_no_blockers():
    recommendation, confidence, reasons = derive_recommendation(
        opportunity_score=84,
        data_confidence=90,
        mandatory_blockers=0,
        deadline_passed=False,
    )
    assert recommendation == "BID"
    assert confidence > 80
    assert reasons


def test_mandatory_blockers_make_recommendation_conditional_or_no_bid():
    conditional, _, _ = derive_recommendation(
        opportunity_score=82,
        data_confidence=85,
        mandatory_blockers=1,
        deadline_passed=False,
    )
    no_bid, confidence, reasons = derive_recommendation(
        opportunity_score=82,
        data_confidence=85,
        mandatory_blockers=2,
        deadline_passed=False,
    )
    assert conditional == "CONDITIONAL"
    assert no_bid == "NO_BID"
    assert confidence > 80
    assert "2" in reasons[0]


def test_expired_deadline_is_an_explainable_no_bid():
    recommendation, confidence, reasons = derive_recommendation(
        opportunity_score=99,
        data_confidence=99,
        mandatory_blockers=0,
        deadline_passed=True,
    )
    assert recommendation == "NO_BID"
    assert confidence >= 95
    assert "παρέλθει" in reasons[0]


def test_actions_cover_requirements_certificates_deadline_and_decision():
    actions = recommended_actions(
        recommendation="CONDITIONAL",
        missing_requirements=[{"title": "ISO 27001", "mandatory": True}],
        missing_certificates=[{"title": "Φορολογική ενημερότητα"}],
        deadline=datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc),
    )
    assert {action["type"] for action in actions} == {
        "REQUIREMENT",
        "CERTIFICATE",
        "DEADLINE",
        "DECISION",
    }
    assert actions[0]["priority"] == "URGENT"


def test_pdf_renderer_always_returns_downloadable_pdf():
    payload = render_bid_report_pdf(
        {
            "title": "Υπηρεσίες GIS",
            "recommendation": "BID",
            "confidence": 88,
            "recommendation_reasons": ["Ισχυρή αντιστοίχιση CPV"],
            "evidence": [{"label": "26PROC000000001"}],
        }
    )
    assert payload.startswith(b"%PDF-")
    assert payload.rstrip().endswith(b"%%EOF")

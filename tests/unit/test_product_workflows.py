from __future__ import annotations

import csv
import zipfile
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from services.alerts.digests import _is_due, _period_key
from services.analytics.profile_classification import CpvCatalogEntry, classify_business_description
from services.analytics.opportunity_scoring import _score_candidate
from services.exports.generate import _write_csv, _write_xlsx


def test_profile_classifier_produces_explainable_cpv_without_generic_stopwords():
    terms = classify_business_description(
        "Παρέχουμε υπηρεσίες λογισμικού GIS, cloud και κυβερνοασφάλειας σε δημόσιους φορείς."
    )
    cpv_values = {term.value for term in terms if term.term_type == "CPV_PREFIX"}
    keywords = {term.value for term in terms if term.term_type == "KEYWORD"}

    assert {"72", "48", "38221000"}.issubset(cpv_values)
    assert "gis" in keywords
    assert "υπηρεσι" not in keywords
    assert "παρεχ" not in keywords
    assert all(term.reason and 0 <= term.confidence <= 1 for term in terms)


def test_gis_only_maps_to_precise_cpv_instead_of_all_it_services():
    terms = classify_business_description("GIS")
    cpv_values = {term.value for term in terms if term.term_type == "CPV_PREFIX"}
    keywords = {term.value for term in terms if term.term_type == "KEYWORD"}

    assert "38221000" in cpv_values
    assert "72" not in cpv_values
    assert "gis" in keywords


def test_profile_classifier_keeps_unknown_technical_acronyms_searchable():
    terms = classify_business_description("Παρέχουμε BIM και IoT εφαρμογές.")
    keywords = {term.value for term in terms if term.term_type == "KEYWORD"}

    assert {"bim", "iot"}.issubset(keywords)


def test_profile_classifier_maps_grass_clearing_to_precise_cpv_and_recall_keyword():
    terms = classify_business_description(
        "Αναλαμβάνουμε αποψιλώσεις, κοπή χόρτων και κλάδεμα δέντρων."
    )
    cpv_values = [term.value for term in terms if term.term_type == "CPV_PREFIX"]
    keywords = {term.value for term in terms if term.term_type == "KEYWORD"}

    assert cpv_values[0] == "77312000"
    assert {"77312000", "77314000", "77341000"}.issubset(cpv_values)
    assert "αποψιλ" in keywords


def test_profile_classifier_searches_catalog_entries_outside_builtin_rules():
    catalog = [
        CpvCatalogEntry(
            code="79961000",
            description_el="Φωτογραφικές υπηρεσίες",
            description_en="Photographic services",
        )
    ]

    terms = classify_business_description(
        "Εξειδικευόμαστε σε φωτογραφικές υπηρεσίες εκδηλώσεων.",
        catalog,
    )

    catalog_match = next(term for term in terms if term.value == "79961000")
    assert catalog_match.source == "CPV_CATALOG"
    assert catalog_match.label == "Φωτογραφικές υπηρεσίες"


def test_title_fallback_does_not_claim_a_perfect_cpv_match():
    score = _score_candidate(
        rule_name="Vegetation profile",
        filters={
            "cpv_prefixes": ["77312000"],
            "keywords": ["αποψιλ"],
            "taxonomy_match_any": True,
        },
        context={
            "title": "Εργασίες αποψίλωσης οικοπέδων",
            "buyer_id": None,
            "cpv_codes": [],
            "nuts_codes": [],
            "amount_gross": Decimal("1000"),
            "act_count": 1,
        },
        latest_act_date=date.today(),
        supplier_count=0,
        as_of=date.today(),
    )

    assert score["cpv_company_fit_score"] == Decimal("75.00")
    assert {"signal": "taxonomy_match_method", "value": "TITLE_KEYWORD_FALLBACK"} in score["evidence"]


def test_digest_due_logic_respects_local_time_and_weekday():
    athens = ZoneInfo("Europe/Athens")
    monday = datetime(2026, 7, 20, 8, 5, tzinfo=athens)
    tuesday = datetime(2026, 7, 21, 8, 5, tzinfo=athens)

    assert _is_due("DAILY_DIGEST", monday, monday.replace(hour=8).time())
    assert _is_due("WEEKLY_DIGEST", monday, monday.replace(hour=8).time())
    assert not _is_due("WEEKLY_DIGEST", tuesday, tuesday.replace(hour=8).time())
    assert _period_key("DAILY_DIGEST", monday) == "2026-07-20"
    assert _period_key("WEEKLY_DIGEST", monday) == "2026-W30"


def test_csv_and_xlsx_exports_are_valid_files(tmp_path):
    rows = [{"title": "Διαγωνισμός", "value": 1250}, {"title": "Cloud", "value": None}]
    csv_path = tmp_path / "export.csv"
    xlsx_path = tmp_path / "export.xlsx"

    _write_csv(csv_path, ["title", "value"], rows)
    _write_xlsx(xlsx_path, ["title", "value"], rows)

    with csv_path.open(encoding="utf-8-sig") as handle:
        assert list(csv.DictReader(handle))[0]["title"] == "Διαγωνισμός"
    assert zipfile.is_zipfile(xlsx_path)
    with zipfile.ZipFile(xlsx_path) as archive:
        assert "xl/worksheets/sheet1.xml" in archive.namelist()
        assert "Διαγωνισμός" in archive.read("xl/worksheets/sheet1.xml").decode()

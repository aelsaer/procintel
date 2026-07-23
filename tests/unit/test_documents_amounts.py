"""Greek amount parsing (§23.4) — the four formats the spec names
explicitly, plus VAT-context detection and the never-guess-without-a-
currency-marker rule."""

from decimal import Decimal

from services.documents.amounts import extract_amounts


def test_period_thousands_comma_decimal_with_trailing_symbol():
    results = extract_amounts("Το ποσό ανέρχεται σε 1.234,56 €")
    assert len(results) == 1
    assert results[0].normalized_amount == Decimal("1234.56")
    assert results[0].currency == "EUR"
    assert results[0].raw_value == "1.234,56 €"


def test_space_thousands_comma_decimal_with_trailing_word():
    results = extract_amounts("Το ποσό ανέρχεται σε 1 234,56 ευρώ")
    assert len(results) == 1
    assert results[0].normalized_amount == Decimal("1234.56")


def test_symbol_first():
    results = extract_amounts("Αξία: € 1.234,56")
    assert len(results) == 1
    assert results[0].normalized_amount == Decimal("1234.56")


def test_us_style_comma_thousands_period_decimal_with_eur_code():
    results = extract_amounts("Amount: 1,234.56 EUR")
    assert len(results) == 1
    assert results[0].normalized_amount == Decimal("1234.56")


def test_thousands_only_no_decimal_part():
    results = extract_amounts("Συνολικό ποσό 150.000 €")
    assert results[0].normalized_amount == Decimal("150000")


def test_vat_inclusive_marker_detected():
    results = extract_amounts("Ποσό 12.500,00 € με ΦΠΑ")
    assert results[0].vat_inclusion_status == "WITH_VAT"


def test_vat_exclusive_marker_detected():
    results = extract_amounts("Ποσό 150.000 € χωρίς ΦΠΑ")
    assert results[0].vat_inclusion_status == "WITHOUT_VAT"


def test_no_vat_marker_is_unknown():
    results = extract_amounts("Ποσό 1.234,56 €")
    assert results[0].vat_inclusion_status == "UNKNOWN"


def test_bare_number_without_currency_marker_is_not_extracted():
    results = extract_amounts("Αριθμός πρωτοκόλλου 1.234,56 χωρίς κανένα νόμισμα δίπλα")
    assert results == []


def test_multiple_amounts_in_one_document_ordered_by_position():
    text = "Καθαρή αξία 1.000,00 € πλέον ΦΠΑ, σύνολο 1.240,00 € με ΦΠΑ"
    results = extract_amounts(text)
    assert len(results) == 2
    assert results[0].normalized_amount == Decimal("1000.00")
    assert results[0].vat_inclusion_status == "WITHOUT_VAT"
    assert results[1].normalized_amount == Decimal("1240.00")
    assert results[1].vat_inclusion_status == "WITH_VAT"

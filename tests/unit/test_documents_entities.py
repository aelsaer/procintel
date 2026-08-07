"""Regex-based entity extraction (§23.3) — ΑΔΑ/ΑΔΑΜ/ΑΦΜ/CPV/MIS/dates/
protocol numbers/duration/lot numbers/units/IBAN."""

from services.documents.entities import (
    extract_ada,
    extract_adam,
    extract_afm,
    extract_cpv,
    extract_dates,
    extract_duration,
    extract_iban,
    extract_lot_numbers,
    extract_mis,
    extract_protocol_numbers,
    extract_procurement_participants,
    extract_unit_quantities,
)

# A real, checksum-valid IBAN example (Greece) used in bank documentation —
# not a real account.
SAMPLE_IBAN = "GR1601101250000000012300695"


def test_extract_ada_locates_and_normalizes():
    results = extract_ada("Σχετ. ΑΔΑ: 7Α1Η465ΦΘΘ-ΘΙΚ για την απόφαση")
    assert len(results) == 1
    assert results[0].normalized_value == "7Α1Η465ΦΘΘ-ΘΙΚ"


def test_extract_adam_reports_category():
    results = extract_adam("ΑΔΑΜ: 25SYMV012345678")
    assert len(results) == 1
    assert results[0].category == "SYMV"
    assert results[0].normalized_value == "25SYMV012345678"


def test_extract_afm_labeled_takes_priority_over_bare_scan():
    results = extract_afm("ΑΦΜ: 090000045")
    assert len(results) == 1
    assert results[0].labeled is True
    assert results[0].checksum_valid is True


def test_extract_afm_bare_valid_checksum_is_still_reported():
    # a bare 9-digit number that happens to pass the checksum, no label
    results = extract_afm("Κωδικός εγγράφου 090000045 στο αρχείο")
    assert len(results) == 1
    assert results[0].labeled is False


def test_extract_afm_bare_invalid_checksum_is_not_reported():
    # 9 digits, no label, fails checksum — too weak a signal, must be dropped
    results = extract_afm("Αριθμός σελίδας 123456789 από 200")
    assert results == []


def test_extract_afm_failed_checksum_is_still_reported_when_labeled():
    # §7.2: a failed checksum does not mean rejection when there IS a label
    results = extract_afm("ΑΦΜ: 123456789")
    assert len(results) == 1
    assert results[0].checksum_valid is False


def test_extract_procurement_participants_requires_role_and_afm():
    results = extract_procurement_participants("ΠΡΟΣΦΕΡΩΝ: ALPHA SYSTEMS Α.Ε., ΑΦΜ: 090000045")
    assert len(results) == 1
    assert results[0].name == "ALPHA SYSTEMS Α.Ε."
    assert results[0].afm == "090000045"
    assert results[0].role == "BIDDER"
    assert results[0].checksum_valid is True


def test_extract_procurement_participants_distinguishes_winner_and_consortium():
    text = """ΑΝΑΔΟΧΟΣ: BETA A.E. ΑΦΜ 090000045
ΜΕΛΟΣ ΤΗΣ ΕΝΩΣΗΣ: GAMMA IKE, Α.Φ.Μ.: 123456789"""
    results = extract_procurement_participants(text)
    assert [result.role for result in results] == ["WINNER", "CONSORTIUM_MEMBER"]
    assert results[1].checksum_valid is False
    assert results[1].confidence < results[0].confidence


def test_extract_procurement_participants_ignores_unscoped_afm():
    assert extract_procurement_participants("Στοιχεία επικοινωνίας εταιρείας ΑΦΜ: 090000045") == []


def test_extract_cpv_with_check_digit():
    results = extract_cpv("CPV: 30192000-1 (είδη γραφείου)")
    assert len(results) == 1
    assert results[0].base_code == "30192000"
    assert results[0].check_digit == "1"


def test_extract_cpv_labeled_without_check_digit():
    results = extract_cpv("Κωδικός CPV 30192000 χωρίς check digit")
    assert len(results) == 1
    assert results[0].base_code == "30192000"
    assert results[0].check_digit is None


def test_extract_mis_requires_explicit_label():
    assert extract_mis("Κωδικός ΟΠΣ: 5012345")[0].raw_value == "5012345"
    assert extract_mis("κάποιος αριθμός 5012345 χωρίς ένδειξη") == []


def test_extract_dates_numeric_and_greek_month_name():
    results = extract_dates("Ημερομηνίες: 15/03/2024 και 12 Ιανουαρίου 2024")
    assert len(results) == 2
    assert (results[0].day, results[0].month, results[0].year) == (15, 3, 2024)
    assert (results[1].day, results[1].month, results[1].year) == (12, 1, 2024)


def test_extract_dates_accepts_ocr_micro_sign_in_september():
    results = extract_dates("Αθήνα, 12 Σεπτεµβρίου 2024")

    assert len(results) == 1
    assert (results[0].day, results[0].month, results[0].year) == (12, 9, 2024)


def test_extract_dates_normalizes_decomposed_greek_month_accents():
    results = extract_dates("Προθεσμία 7 μαΐου 2026")

    assert len(results) == 1
    assert (results[0].day, results[0].month, results[0].year) == (7, 5, 2026)


def test_extract_dates_rejects_invalid_month():
    assert extract_dates("13/13/2024") == []


def test_extract_protocol_number():
    results = extract_protocol_numbers("Αρ. Πρωτ.: 12345/2024")
    assert len(results) == 1
    assert results[0].raw_value == "12345/2024"


def test_extract_duration_months():
    results = extract_duration("Διάρκεια σύμβασης: 12 μήνες")
    assert len(results) == 1
    assert results[0].quantity == 12
    assert results[0].unit == "MONTHS"


def test_extract_duration_days_and_years():
    results = extract_duration("3 ημέρες, 2 έτη")
    units = {(r.quantity, r.unit) for r in results}
    assert (3, "DAYS") in units
    assert (2, "YEARS") in units


def test_extract_lot_numbers_greek_and_english():
    results = extract_lot_numbers("Τμήμα 3 και Lot 5")
    numbers = {r.lot_number for r in results}
    assert numbers == {3, 5}


def test_extract_unit_quantities():
    results = extract_unit_quantities("Ποσότητα: 10 τεμ και 5 kg")
    found = {(r.quantity, r.unit) for r in results}
    assert ("10", "τεμ") in found
    assert ("5", "kg") in found


def test_extract_iban_valid_checksum():
    results = extract_iban(f"IBAN: {SAMPLE_IBAN}")
    assert len(results) == 1
    assert results[0].normalized_value == SAMPLE_IBAN
    assert results[0].checksum_valid is True


def test_extract_iban_invalid_checksum_still_reported_but_flagged():
    corrupted = SAMPLE_IBAN[:-1] + ("0" if SAMPLE_IBAN[-1] != "0" else "1")
    results = extract_iban(f"IBAN: {corrupted}")
    assert len(results) == 1
    assert results[0].checksum_valid is False

from services.search_index.lexical import (
    lexical_query_matches,
    normalize_lexical_text,
    query_concept_pattern,
    query_prefilter,
    query_token_patterns,
)


def test_lexical_search_is_case_and_accent_insensitive():
    assert normalize_lexical_text("ΚΑΘΑΡΙΣΜΟΎ") == "καθαρισμου"
    assert lexical_query_matches("καθαρισμού οικοπέδων", "Εργασίες καθαρισμού ΟΙΚΟΠΕΔΙΚΩΝ εκτάσεων")


def test_lexical_search_requires_every_query_term():
    assert lexical_query_matches("ψηφιακή χαρτογράφηση", "Ψηφιακό σύστημα χαρτογράφησης")
    assert not lexical_query_matches("ψηφιακή χαρτογράφηση", "Γενικές ψηφιακές υπηρεσίες")


def test_short_terms_use_boundaries_and_gis_understands_common_products():
    assert lexical_query_matches("GIS", "Συντήρηση ArcGIS")
    assert lexical_query_matches("GIS", "Ανάπτυξη WebGIS πλατφόρμας")
    assert not lexical_query_matches("GIS", "Υπηρεσίες LOGISTICS")
    assert query_token_patterns("GIS") == [r"(^| )((arc|q|web)?gis)( |$)"]
    assert query_prefilter("GIS") == "%gis%"


def test_business_concept_requires_every_word():
    pattern = query_concept_pattern("καθαρισμός οικοπέδων")

    assert pattern is not None
    assert lexical_query_matches("καθαρισμός οικοπέδων", "Καθαρισμοί δημοτικών οικοπέδων")
    assert not lexical_query_matches("καθαρισμός οικοπέδων", "Καθαρισμός υπολογιστών")
    assert pattern.count("(?=.*") == 2

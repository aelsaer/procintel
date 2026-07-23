from services.ingestion.connectors.diavgeia.resolve import _score_search_results

ORG = "ΔΗΜΟΣ ΔΟΚΙΜΗΣ"
TITLE = "Παροχή υπηρεσιών καθαρισμού δημοσίων κτιρίων"


def test_single_matching_result_scored():
    results = [{"organizationLabel": ORG, "subject": TITLE, "ada": "X1-ABC"}]
    scored = _score_search_results(results, organization_query=ORG, title_query=TITLE)
    assert len(scored) == 1
    assert scored[0][2]["ada"] == "X1-ABC"


def test_weak_organization_match_excluded():
    results = [{"organizationLabel": "ΤΕΛΕΙΩΣ ΑΣΧΕΤΟΣ ΦΟΡΕΑΣ", "subject": TITLE, "ada": "X1-ABC"}]
    scored = _score_search_results(results, organization_query=ORG, title_query=TITLE)
    assert scored == []


def test_weak_title_match_excluded():
    results = [{"organizationLabel": ORG, "subject": "Εντελώς άσχετο έργο", "ada": "X1-ABC"}]
    scored = _score_search_results(results, organization_query=ORG, title_query=TITLE)
    assert scored == []


def test_multiple_matches_all_returned_for_caller_to_judge_ambiguous():
    results = [
        {"organizationLabel": ORG, "subject": TITLE, "ada": "X1-ABC"},
        {"organizationLabel": ORG, "subject": TITLE, "ada": "X2-DEF"},
    ]
    scored = _score_search_results(results, organization_query=ORG, title_query=TITLE)
    assert len(scored) == 2

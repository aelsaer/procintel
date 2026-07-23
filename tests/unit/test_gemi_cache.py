from datetime import datetime, timedelta, timezone

from services.ingestion.connectors.gemi.cache import (
    ACTIVE_REFRESH,
    NEGATIVE_RESULT_REFRESH,
    TRANSITION_REFRESH,
    is_stable_status,
    should_refresh,
)

NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)


def test_new_company_always_refreshes():
    assert should_refresh(last_checked_at=None, company_status=None, now=NOW) is True


def test_stable_status_recognized_case_insensitively():
    # is_stable_status receives the *canonical* code normalize.py already
    # produced (lexicon.normalize_company_status) — raw Greek labels like
    # "ΕΝΕΡΓΗ" are normalized to "ACTIVE" upstream, not recognized here
    # directly (see test_gemi_normalize.py for that normalization step).
    assert is_stable_status("active") is True
    assert is_stable_status(" ACTIVE ".strip()) is True
    assert is_stable_status("IN_LIQUIDATION") is False
    assert is_stable_status("ΥΠΟ ΕΚΚΑΘΑΡΙΣΗ") is False  # raw label, not canonical — correctly not stable
    assert is_stable_status(None) is False


def test_active_company_refreshes_only_after_30_days():
    just_under = NOW - (ACTIVE_REFRESH - timedelta(days=1))
    just_over = NOW - (ACTIVE_REFRESH + timedelta(days=1))
    assert should_refresh(last_checked_at=just_under, company_status="ACTIVE", now=NOW) is False
    assert should_refresh(last_checked_at=just_over, company_status="ACTIVE", now=NOW) is True


def test_company_in_transition_refreshes_sooner_than_active():
    between_transition_and_active = NOW - (TRANSITION_REFRESH + timedelta(days=1))
    assert should_refresh(last_checked_at=between_transition_and_active, company_status="IN_LIQUIDATION", now=NOW) is True
    assert should_refresh(last_checked_at=between_transition_and_active, company_status="ACTIVE", now=NOW) is False


def test_negative_result_is_not_permanently_cached():
    just_under = NOW - (NEGATIVE_RESULT_REFRESH - timedelta(days=1))
    just_over = NOW - (NEGATIVE_RESULT_REFRESH + timedelta(days=1))
    assert should_refresh(last_checked_at=just_under, company_status=None, now=NOW) is False
    assert should_refresh(last_checked_at=just_over, company_status=None, now=NOW) is True

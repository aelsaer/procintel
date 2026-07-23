import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from services.ingestion.connectors.diavgeia import resolve as diavgeia_resolve
from services.ingestion.connectors.mef import resolve as mef_resolve


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


async def test_existing_diavgeia_decision_is_linked_without_http(monkeypatch):
    origin_act_id = uuid.uuid4()
    decision_act_id = uuid.uuid4()
    conn = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult(decision_act_id)), commit=AsyncMock())
    link = AsyncMock()
    join = AsyncMock()
    monkeypatch.setattr(diavgeia_resolve, "_link_to_origin", link)
    monkeypatch.setattr(diavgeia_resolve, "_join_origin_process", join)

    result = await diavgeia_resolve.link_existing_decision_for_ada(
        conn,
        ada="6ΙΖ07Λ7-ΕΨΒ",
        origin_act_id=origin_act_id,
    )

    assert result == decision_act_id
    link.assert_awaited_once()
    join.assert_awaited_once_with(
        conn,
        origin_act_id=origin_act_id,
        decision_act_id=decision_act_id,
    )
    conn.commit.assert_awaited_once()


async def test_existing_diavgeia_identifier_on_origin_is_not_self_linked(monkeypatch):
    origin_act_id = uuid.uuid4()
    conn = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult(origin_act_id)), commit=AsyncMock())
    link = AsyncMock()
    monkeypatch.setattr(diavgeia_resolve, "_link_to_origin", link)

    result = await diavgeia_resolve.link_existing_decision_for_ada(
        conn,
        ada="6ΙΖ07Λ7-ΕΨΒ",
        origin_act_id=origin_act_id,
    )

    assert result is None
    link.assert_not_awaited()
    conn.commit.assert_not_awaited()


async def test_unlinked_mef_expense_is_retried_from_local_evidence(monkeypatch):
    contractor_entity_id = uuid.uuid4()
    expense_id = uuid.uuid4()
    linked_act_id = uuid.uuid4()
    source_record_id = uuid.uuid4()
    row = SimpleNamespace(
        id=expense_id,
        mef_organization_id=uuid.uuid4(),
        related_ada_raw="6ΙΖ07Λ7-ΕΨΒ",
        amount=100,
        expense_date=None,
        source_record_id=source_record_id,
    )
    conn = SimpleNamespace(execute=AsyncMock(return_value=_RowsResult([row])), commit=AsyncMock())
    resolve_link = AsyncMock(return_value=(linked_act_id, "ADA_AND_AFM", 0.99))
    monkeypatch.setattr(mef_resolve, "resolve_expense_link", resolve_link)

    linked = await mef_resolve.relink_existing_expenses_for_contractor(
        conn,
        contractor_entity_id=contractor_entity_id,
        afm_normalized="090000045",
    )

    assert linked == 1
    resolve_link.assert_awaited_once()
    conn.commit.assert_awaited_once()

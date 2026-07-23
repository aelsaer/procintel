"""Authenticated alert rules, inbox, targets and delivery history."""

from __future__ import annotations

import uuid
from datetime import datetime, time, timezone
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.auth.jwt_verifier import AuthenticatedUser
from packages.domain.tables import (
    alert_delivery_targets,
    alert_digest_runs,
    alert_events,
    alert_rules,
    audit_log,
    webhook_deliveries,
)

from ..auth import get_current_user, require_role
from ..db import get_tenant_scoped_conn
from ..workspace import ensure_workspace_user, tenant_uuid

router = APIRouter(prefix="/v1/alert-rules", tags=["alerts"])
_NON_VIEWER_ROLES = ("OWNER", "ADMIN", "ANALYST", "SALES", "BID_MANAGER", "API_CLIENT")
_EVENT_TYPES = {
    "opportunity.created", "opportunity.updated", "contract.created", "contract.modified",
    "contract.expiring", "payment.detected", "company.status_changed", "buyer.new_procurement",
}
_SCHEDULES = {"IMMEDIATE", "DAILY_DIGEST", "WEEKLY_DIGEST"}
_CHANNELS = {"EMAIL", "IN_APP", "WEBHOOK", "TEAMS", "SLACK"}


class AlertTargetRequest(BaseModel):
    channel_type: str
    target: str = Field(min_length=3, max_length=2000)
    secret: str | None = Field(default=None, max_length=500)
    is_active: bool = True


class AlertTargetResponse(BaseModel):
    id: str
    channel_type: str
    target: str
    is_active: bool


class AlertRuleResponse(BaseModel):
    id: str
    name: str
    event_types: list[str]
    filters: dict[str, Any]
    schedule: str
    delivery_channels: list[str]
    timezone: str
    digest_time: time
    is_active: bool
    targets: list[AlertTargetResponse]
    event_count: int = 0
    unread_count: int = 0
    created_at: datetime
    updated_at: datetime


class AlertRuleCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    event_types: list[str]
    filters: dict[str, Any] = Field(default_factory=dict)
    schedule: str = "IMMEDIATE"
    delivery_channels: list[str] = Field(default_factory=lambda: ["IN_APP"])
    timezone: str = "Europe/Athens"
    digest_time: time = time(8, 0)
    is_active: bool = True
    targets: list[AlertTargetRequest] = Field(default_factory=list)


class AlertEventResponse(BaseModel):
    id: str
    alert_rule_id: str
    rule_name: str
    canonical_object_type: str
    canonical_object_id: str
    event_type: str
    payload: dict[str, Any]
    triggered_at: datetime
    delivered_at: datetime | None
    read_at: datetime | None


class AlertPreviewResponse(BaseModel):
    matching_count: int
    sample: list[dict[str, Any]]
    explanation: list[str]


class DeliveryHistoryResponse(BaseModel):
    id: str
    alert_event_id: str
    channel: str
    status: str
    attempt_count: int
    response_status: int | None
    last_attempt_at: datetime | None
    next_retry_at: datetime | None
    created_at: datetime


class DigestHistoryResponse(BaseModel):
    id: str
    alert_rule_id: str | None
    schedule: str
    event_count: int
    status: str
    channels: list[str]
    created_at: datetime
    delivered_at: datetime | None


def _as_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid UUID") from exc


def _validate(body: AlertRuleCreateRequest) -> None:
    unknown_events = set(body.event_types) - _EVENT_TYPES
    unknown_channels = set(body.delivery_channels) - _CHANNELS
    if not body.event_types or unknown_events:
        raise HTTPException(status_code=422, detail=f"Invalid event types: {sorted(unknown_events)}")
    if body.schedule not in _SCHEDULES:
        raise HTTPException(status_code=422, detail=f"Invalid schedule: {body.schedule}")
    if unknown_channels:
        raise HTTPException(status_code=422, detail=f"Invalid delivery channels: {sorted(unknown_channels)}")
    for target in body.targets:
        if target.channel_type not in _CHANNELS - {"IN_APP"}:
            raise HTTPException(status_code=422, detail=f"Invalid target channel: {target.channel_type}")
        if target.channel_type == "EMAIL" and "@" not in target.target:
            raise HTTPException(status_code=422, detail="Invalid email delivery target")
        if target.channel_type in {"WEBHOOK", "TEAMS", "SLACK"}:
            try:
                HttpUrl(target.target)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Webhook targets must be valid HTTP(S) URLs") from exc


async def _serialize_rule(conn: AsyncConnection, tenant_id: uuid.UUID, rule_id: uuid.UUID) -> AlertRuleResponse:
    row = (await conn.execute(
        sa.select(
            alert_rules,
            sa.func.count(sa.distinct(alert_events.c.id)).label("event_count"),
            sa.func.count(sa.distinct(alert_events.c.id)).filter(alert_events.c.read_at.is_(None)).label("unread_count"),
        )
        .outerjoin(alert_events, alert_events.c.alert_rule_id == alert_rules.c.id)
        .where(alert_rules.c.id == rule_id, alert_rules.c.tenant_id == tenant_id)
        .group_by(alert_rules.c.id)
    )).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    target_rows = (await conn.execute(
        sa.select(alert_delivery_targets).where(alert_delivery_targets.c.alert_rule_id == rule_id).order_by(alert_delivery_targets.c.created_at)
    )).all()
    return AlertRuleResponse(
        id=str(row.id), name=row.name, event_types=row.event_types, filters=row.filters,
        schedule=row.schedule, delivery_channels=row.delivery_channels, timezone=row.timezone,
        digest_time=row.digest_time, is_active=row.is_active, event_count=row.event_count,
        unread_count=row.unread_count, created_at=row.created_at, updated_at=row.updated_at,
        targets=[AlertTargetResponse(id=str(target.id), channel_type=target.channel_type, target=target.target, is_active=target.is_active) for target in target_rows],
    )


@router.get("", response_model=list[AlertRuleResponse])
async def list_alert_rules(
    include_archived: bool = False,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> list[AlertRuleResponse]:
    tenant_id = tenant_uuid(user)
    query = sa.select(alert_rules.c.id).where(alert_rules.c.tenant_id == tenant_id)
    if not include_archived:
        query = query.where(sa.not_(alert_rules.c.name.startswith("[Archived] ")))
    ids = (await conn.execute(query.order_by(alert_rules.c.updated_at.desc()))).scalars().all()
    return [await _serialize_rule(conn, tenant_id, rule_id) for rule_id in ids]


@router.post("", response_model=AlertRuleResponse, status_code=201)
async def create_alert_rule(
    body: AlertRuleCreateRequest,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_NON_VIEWER_ROLES)),
) -> AlertRuleResponse:
    _validate(body)
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    rule_id = uuid.uuid4()
    await conn.execute(alert_rules.insert().values(
        id=rule_id, tenant_id=tenant_id, user_id=user_id, name=body.name,
        event_types=body.event_types, filters=body.filters, schedule=body.schedule,
        delivery_channels=body.delivery_channels, timezone=body.timezone,
        digest_time=body.digest_time, is_active=body.is_active,
    ))
    for target in body.targets:
        await conn.execute(alert_delivery_targets.insert().values(
            id=uuid.uuid4(), alert_rule_id=rule_id, channel_type=target.channel_type,
            target=target.target, secret=target.secret, is_active=target.is_active,
        ))
    await conn.execute(audit_log.insert().values(
        id=uuid.uuid4(), tenant_id=tenant_id, actor_user_id=user_id,
        action="alert_rule.created", object_type="alert_rule", object_id=rule_id,
        details={"schedule": body.schedule, "channels": body.delivery_channels},
    ))
    return await _serialize_rule(conn, tenant_id, rule_id)


@router.get("/events", response_model=list[AlertEventResponse])
async def list_alert_events(
    unread_only: bool = False, limit: int = Query(default=100, ge=1, le=500),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> list[AlertEventResponse]:
    query = (
        sa.select(alert_events, alert_rules.c.name.label("rule_name"))
        .join(alert_rules, alert_rules.c.id == alert_events.c.alert_rule_id)
        .where(alert_rules.c.tenant_id == tenant_uuid(user))
        .order_by(alert_events.c.triggered_at.desc()).limit(limit)
    )
    if unread_only:
        query = query.where(alert_events.c.read_at.is_(None))
    rows = (await conn.execute(query)).all()
    return [AlertEventResponse(
        id=str(row.id), alert_rule_id=str(row.alert_rule_id), rule_name=row.rule_name,
        canonical_object_type=row.canonical_object_type, canonical_object_id=str(row.canonical_object_id),
        event_type=row.event_type, payload=row.payload, triggered_at=row.triggered_at,
        delivered_at=row.delivered_at, read_at=row.read_at,
    ) for row in rows]


@router.patch("/events/{event_id}/read", response_model=AlertEventResponse)
async def mark_event_read(
    event_id: str,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> AlertEventResponse:
    target = _as_uuid(event_id)
    row = (await conn.execute(
        alert_events.update().where(
            alert_events.c.id == target,
            alert_events.c.alert_rule_id.in_(sa.select(alert_rules.c.id).where(alert_rules.c.tenant_id == tenant_uuid(user))),
        ).values(read_at=datetime.now(timezone.utc)).returning(alert_events)
    )).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Alert event not found")
    rule_name = (await conn.execute(sa.select(alert_rules.c.name).where(alert_rules.c.id == row.alert_rule_id))).scalar_one()
    return AlertEventResponse(
        id=str(row.id), alert_rule_id=str(row.alert_rule_id), rule_name=rule_name,
        canonical_object_type=row.canonical_object_type, canonical_object_id=str(row.canonical_object_id),
        event_type=row.event_type, payload=row.payload, triggered_at=row.triggered_at,
        delivered_at=row.delivered_at, read_at=row.read_at,
    )


@router.get("/delivery-history", response_model=list[DeliveryHistoryResponse])
async def delivery_history(
    limit: int = Query(default=100, ge=1, le=500),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> list[DeliveryHistoryResponse]:
    rows = (await conn.execute(sa.select(webhook_deliveries).where(
        webhook_deliveries.c.tenant_id == tenant_uuid(user),
    ).order_by(webhook_deliveries.c.created_at.desc()).limit(limit))).all()
    return [DeliveryHistoryResponse(
        id=str(row.id), alert_event_id=str(row.alert_event_id), channel="WEBHOOK",
        status=row.status, attempt_count=row.attempt_count, response_status=row.response_status,
        last_attempt_at=row.last_attempt_at, next_retry_at=row.next_retry_at, created_at=row.created_at,
    ) for row in rows]


@router.get("/digest-history", response_model=list[DigestHistoryResponse])
async def digest_history(
    limit: int = Query(default=100, ge=1, le=500),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> list[DigestHistoryResponse]:
    rows = (await conn.execute(sa.select(alert_digest_runs).where(
        alert_digest_runs.c.tenant_id == tenant_uuid(user),
    ).order_by(alert_digest_runs.c.created_at.desc()).limit(limit))).all()
    return [DigestHistoryResponse(
        id=str(row.id), alert_rule_id=str(row.alert_rule_id) if row.alert_rule_id else None,
        schedule=row.schedule, event_count=row.event_count, status=row.status,
        channels=row.channels or [], created_at=row.created_at, delivered_at=row.delivered_at,
    ) for row in rows]


@router.get("/{rule_id}", response_model=AlertRuleResponse)
async def get_alert_rule(
    rule_id: str,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> AlertRuleResponse:
    return await _serialize_rule(conn, tenant_uuid(user), _as_uuid(rule_id))


@router.put("/{rule_id}", response_model=AlertRuleResponse)
async def update_alert_rule(
    rule_id: str, body: AlertRuleCreateRequest,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_NON_VIEWER_ROLES)),
) -> AlertRuleResponse:
    _validate(body)
    target_id = _as_uuid(rule_id)
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    result = await conn.execute(alert_rules.update().where(
        alert_rules.c.id == target_id, alert_rules.c.tenant_id == tenant_id,
    ).values(
        name=body.name, event_types=body.event_types, filters=body.filters,
        schedule=body.schedule, delivery_channels=body.delivery_channels,
        timezone=body.timezone, digest_time=body.digest_time,
        is_active=body.is_active, updated_at=datetime.now(timezone.utc),
    ))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    await conn.execute(alert_delivery_targets.delete().where(alert_delivery_targets.c.alert_rule_id == target_id))
    for target in body.targets:
        await conn.execute(alert_delivery_targets.insert().values(
            id=uuid.uuid4(), alert_rule_id=target_id, channel_type=target.channel_type,
            target=target.target, secret=target.secret, is_active=target.is_active,
        ))
    await conn.execute(audit_log.insert().values(
        id=uuid.uuid4(), tenant_id=tenant_id, actor_user_id=user_id,
        action="alert_rule.updated", object_type="alert_rule", object_id=target_id,
        details={"schedule": body.schedule, "channels": body.delivery_channels},
    ))
    return await _serialize_rule(conn, tenant_id, target_id)


@router.delete("/{rule_id}", status_code=204)
async def delete_alert_rule(
    rule_id: str,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_NON_VIEWER_ROLES)),
) -> Response:
    target_id = _as_uuid(rule_id)
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    if not (await conn.execute(sa.select(alert_rules.c.id).where(alert_rules.c.id == target_id, alert_rules.c.tenant_id == tenant_id))).first():
        raise HTTPException(status_code=404, detail="Alert rule not found")
    # Preserve event history; a deleted rule becomes inactive and explicitly archived.
    await conn.execute(alert_rules.update().where(alert_rules.c.id == target_id).values(
        is_active=False, name=sa.func.concat("[Archived] ", alert_rules.c.name), updated_at=datetime.now(timezone.utc),
    ))
    await conn.execute(audit_log.insert().values(
        id=uuid.uuid4(), tenant_id=tenant_id, actor_user_id=user_id,
        action="alert_rule.archived", object_type="alert_rule", object_id=target_id,
    ))
    return Response(status_code=204)


@router.post("/{rule_id}/preview", response_model=AlertPreviewResponse)
async def preview_alert_rule(
    rule_id: str,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> AlertPreviewResponse:
    rule = await _serialize_rule(conn, tenant_uuid(user), _as_uuid(rule_id))
    filters = rule.filters
    rows = (await conn.execute(sa.text(
        """
        SELECT pp.id, pp.title, COALESCE(pp.estimated_value, MAX(a.amount_gross)) AS amount,
               ARRAY_REMOVE(ARRAY_AGG(DISTINCT cpv.cpv_code), NULL) AS cpv_codes
        FROM procurement_processes pp
        JOIN procurement_acts a ON a.process_id = pp.id AND a.is_current = TRUE
        LEFT JOIN act_cpv_codes cpv ON cpv.act_id = a.id
        WHERE a.act_type IN ('REQUEST', 'NOTICE')
        GROUP BY pp.id, pp.title, pp.estimated_value
        ORDER BY MAX(COALESCE(a.publication_date, a.submission_date, a.decision_date)) DESC NULLS LAST
        LIMIT 250
        """
    ))).all()
    cpv_prefixes = filters.get("cpv_prefixes") or ([filters["cpv_prefix"]] if filters.get("cpv_prefix") else [])
    amount_min = filters.get("amount_min")
    amount_max = filters.get("amount_max")
    matches = []
    for row in rows:
        if cpv_prefixes and not any(any(code.startswith(prefix) for prefix in cpv_prefixes) for code in (row.cpv_codes or [])):
            continue
        if amount_min is not None and (row.amount is None or row.amount < amount_min):
            continue
        if amount_max is not None and (row.amount is None or row.amount > amount_max):
            continue
        matches.append({"process_id": str(row.id), "title": row.title, "amount": str(row.amount) if row.amount is not None else None})
    explanation = [f"CPV: {', '.join(cpv_prefixes)}" if cpv_prefixes else "Όλες οι κατηγορίες CPV"]
    if amount_min is not None or amount_max is not None:
        explanation.append(f"Εύρος αξίας: {amount_min or 0} έως {amount_max or 'χωρίς όριο'}")
    return AlertPreviewResponse(matching_count=len(matches), sample=matches[:10], explanation=explanation)

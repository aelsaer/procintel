"""Plans, trial provisioning, billing, entitlements, support and public status."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.auth.jwt_verifier import AuthenticatedUser
from packages.domain.tables import (
    account_success_tasks,
    audit_log,
    entitlement_usage,
    saas_plans,
    service_incidents,
    support_tickets,
    tenant_subscriptions,
)
from services.product.entitlements import effective_entitlements, verify_stripe_signature

from ..auth import get_current_user, require_role
from ..db import get_conn, get_tenant_scoped_conn
from ..deps import get_http_client
from ..workspace import ensure_workspace_user, tenant_uuid

router = APIRouter(tags=["commercial"])
_ADMIN_ROLES = ("OWNER", "ADMIN")


class PlanResponse(BaseModel):
    code: str
    name: str
    description: str
    monthly_price_cents: int | None
    annual_price_cents: int | None
    currency: str
    trial_days: int
    entitlements: dict[str, Any]


class ProvisionRequest(BaseModel):
    organization_name: str | None = Field(default=None, max_length=200)


class ProvisionResponse(BaseModel):
    tenant_id: str
    user_id: str
    created: bool


class SubscriptionResponse(BaseModel):
    plan: PlanResponse
    status: str
    billing_provider: str
    trial_ends_at: datetime | None
    current_period_end: datetime | None
    cancel_at_period_end: bool
    entitlements: dict[str, Any]
    usage: dict[str, int]


class CheckoutRequest(BaseModel):
    plan_code: str
    billing_cycle: str = "MONTHLY"


class CheckoutResponse(BaseModel):
    mode: str
    checkout_url: str | None
    message: str


class SupportTicketRequest(BaseModel):
    subject: str = Field(min_length=3, max_length=300)
    description: str = Field(min_length=5, max_length=20_000)
    category: str = Field(default="PRODUCT", max_length=60)
    priority: str = Field(default="NORMAL", max_length=30)


class SupportTicketResponse(BaseModel):
    id: str
    subject: str
    description: str
    category: str
    priority: str
    status: str
    response_due_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SuccessTaskResponse(BaseModel):
    id: str
    title: str
    description: str | None
    status: str
    due_at: datetime | None
    completed_at: datetime | None


class StatusResponse(BaseModel):
    status: str
    generated_at: datetime
    components: list[dict[str, str]]
    incidents: list[dict[str, Any]]


def _plan(row: Any) -> PlanResponse:
    return PlanResponse(
        code=row.code,
        name=row.name,
        description=row.description,
        monthly_price_cents=row.monthly_price_cents,
        annual_price_cents=row.annual_price_cents,
        currency=row.currency,
        trial_days=row.trial_days,
        entitlements=dict(row.entitlements or {}),
    )


async def _ensure_subscription(
    conn: AsyncConnection,
    *,
    tenant_id: uuid.UUID,
) -> Any:
    row = (
        await conn.execute(
            sa.select(tenant_subscriptions, saas_plans)
            .join(saas_plans, saas_plans.c.code == tenant_subscriptions.c.plan_code)
            .where(tenant_subscriptions.c.tenant_id == tenant_id)
        )
    ).first()
    if row is not None:
        return row
    plan = (
        await conn.execute(sa.select(saas_plans).where(saas_plans.c.code == "STARTER"))
    ).one()
    now = datetime.now(timezone.utc)
    trial_end = now + timedelta(days=plan.trial_days)
    await conn.execute(
        tenant_subscriptions.insert().values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            plan_code="STARTER",
            status="TRIALING",
            billing_provider="MANUAL",
            trial_started_at=now,
            trial_ends_at=trial_end,
            current_period_start=now,
            current_period_end=trial_end,
        )
    )
    return (
        await conn.execute(
            sa.select(tenant_subscriptions, saas_plans)
            .join(saas_plans, saas_plans.c.code == tenant_subscriptions.c.plan_code)
            .where(tenant_subscriptions.c.tenant_id == tenant_id)
        )
    ).one()


async def _subscription_response(
    conn: AsyncConnection,
    *,
    tenant_id: uuid.UUID,
) -> SubscriptionResponse:
    row = await _ensure_subscription(conn, tenant_id=tenant_id)
    usage_rows = (
        await conn.execute(
            sa.select(entitlement_usage.c.metric_code, entitlement_usage.c.usage_count)
            .where(
                entitlement_usage.c.tenant_id == tenant_id,
                entitlement_usage.c.period_start
                <= datetime.now(timezone.utc).date(),
                entitlement_usage.c.period_end
                > datetime.now(timezone.utc).date(),
            )
        )
    ).all()
    entitlements = effective_entitlements(
        dict(row.entitlements or {}),
        dict(row.entitlements_override or {}),
    )
    return SubscriptionResponse(
        plan=_plan(row),
        status=row.status,
        billing_provider=row.billing_provider,
        trial_ends_at=row.trial_ends_at,
        current_period_end=row.current_period_end,
        cancel_at_period_end=row.cancel_at_period_end,
        entitlements=entitlements,
        usage={item.metric_code: int(item.usage_count) for item in usage_rows},
    )


@router.get("/v1/commercial/plans", response_model=list[PlanResponse])
async def list_plans(
    conn: AsyncConnection = Depends(get_conn),
) -> list[PlanResponse]:
    rows = (
        await conn.execute(
            sa.select(saas_plans)
            .where(saas_plans.c.is_public.is_(True))
            .order_by(saas_plans.c.display_order)
        )
    ).all()
    return [_plan(row) for row in rows]


@router.post("/v1/commercial/provision", response_model=ProvisionResponse)
async def provision_organization(
    body: ProvisionRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: AsyncConnection = Depends(get_conn),
) -> ProvisionResponse:
    if user.auth_method == "API_KEY":
        raise HTTPException(status_code=403, detail="API keys cannot provision organizations")
    if not user.email or (user.auth_method == "OIDC" and not user.email_verified):
        raise HTTPException(status_code=422, detail="A verified email claim is required")
    issuer = os.environ.get("OIDC_ISSUER_URL", "procintel-local").rstrip("/")
    row = (
        await conn.execute(
            sa.text(
                """
                SELECT *
                FROM procintel_provision_tenant(
                    :issuer, :subject, :email, :organization_name
                )
                """
            ),
            {
                "issuer": issuer,
                "subject": user.subject,
                "email": user.email,
                "organization_name": body.organization_name or "",
            },
        )
    ).one()
    await conn.execute(
        sa.text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(row.tenant_id)},
    )
    await conn.execute(
        audit_log.insert().values(
            id=uuid.uuid4(),
            tenant_id=row.tenant_id,
            actor_user_id=row.user_id,
            action="organization.provisioned" if row.created else "organization.login_bound",
            object_type="tenant",
            object_id=row.tenant_id,
            details={"issuer": issuer, "subject": user.subject},
        )
    )
    await conn.commit()
    return ProvisionResponse(
        tenant_id=str(row.tenant_id),
        user_id=str(row.user_id),
        created=row.created,
    )


@router.get("/v1/commercial/subscription", response_model=SubscriptionResponse)
async def get_subscription(
    user: AuthenticatedUser = Depends(get_current_user),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> SubscriptionResponse:
    return await _subscription_response(conn, tenant_id=tenant_uuid(user))


@router.post("/v1/commercial/checkout", response_model=CheckoutResponse)
async def create_checkout(
    body: CheckoutRequest,
    user: AuthenticatedUser = Depends(require_role(*_ADMIN_ROLES)),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    http_client: httpx.AsyncClient = Depends(get_http_client),
) -> CheckoutResponse:
    plan_code = body.plan_code.upper()
    plan = (
        await conn.execute(
            sa.select(saas_plans).where(
                saas_plans.c.code == plan_code,
                saas_plans.c.is_public.is_(True),
            )
        )
    ).first()
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    if plan.monthly_price_cents is None:
        user_id = await ensure_workspace_user(conn, user)
        await conn.execute(
            support_tickets.insert().values(
                id=uuid.uuid4(),
                tenant_id=tenant_uuid(user),
                created_by=user_id,
                subject=f"Enterprise plan request: {plan.name}",
                description="Customer requested a sales-assisted enterprise subscription.",
                category="BILLING",
                priority="HIGH",
                response_due_at=datetime.now(timezone.utc) + timedelta(hours=8),
            )
        )
        return CheckoutResponse(
            mode="SALES_ASSISTED",
            checkout_url=None,
            message="Το αίτημα καταχωρίστηκε. Η ομάδα λογαριασμού θα επικοινωνήσει μαζί σας.",
        )
    stripe_secret = os.environ.get("STRIPE_SECRET_KEY")
    price_key = f"STRIPE_PRICE_{plan_code}_{body.billing_cycle.upper()}"
    price_id = os.environ.get(price_key)
    if not stripe_secret or not price_id:
        raise HTTPException(
            status_code=503,
            detail=f"Stripe billing is not configured ({price_key})",
        )
    web_origin = os.environ.get("PROCINTEL_WEB_ORIGIN", "http://localhost:3000").rstrip("/")
    stripe_response = await http_client.post(
        "https://api.stripe.com/v1/checkout/sessions",
        headers={"Authorization": f"Bearer {stripe_secret}"},
        data={
            "mode": "subscription",
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
            "success_url": f"{web_origin}/settings?billing=success",
            "cancel_url": f"{web_origin}/settings?billing=cancel",
            "client_reference_id": str(tenant_uuid(user)),
            "metadata[tenant_id]": str(tenant_uuid(user)),
            "metadata[plan_code]": plan_code,
            "subscription_data[metadata][tenant_id]": str(tenant_uuid(user)),
            "subscription_data[metadata][plan_code]": plan_code,
        },
    )
    stripe_response.raise_for_status()
    payload = stripe_response.json()
    checkout_url = payload.get("url")
    if not checkout_url:
        raise HTTPException(status_code=502, detail="Stripe did not return a checkout URL")
    return CheckoutResponse(
        mode="STRIPE",
        checkout_url=checkout_url,
        message="Continue to secure checkout.",
    )


@router.post("/v1/commercial/webhooks/stripe", include_in_schema=False)
async def stripe_webhook(
    request: Request,
    conn: AsyncConnection = Depends(get_conn),
) -> dict[str, bool]:
    payload = await request.body()
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    signature = request.headers.get("stripe-signature", "")
    if not secret or not verify_stripe_signature(payload, signature, secret):
        raise HTTPException(status_code=400, detail="Invalid Stripe signature")
    event = json.loads(payload)
    event_type = event.get("type")
    obj = event.get("data", {}).get("object", {})
    metadata = obj.get("metadata", {}) if isinstance(obj, dict) else {}
    if event_type == "checkout.session.completed":
        try:
            target_tenant = uuid.UUID(metadata["tenant_id"])
            plan_code = metadata["plan_code"]
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Stripe metadata is incomplete") from exc
        now = datetime.now(timezone.utc)
        await conn.execute(
            pg_insert(tenant_subscriptions)
            .values(
                id=uuid.uuid4(),
                tenant_id=target_tenant,
                plan_code=plan_code,
                status="ACTIVE",
                billing_provider="STRIPE",
                provider_customer_id=obj.get("customer"),
                provider_subscription_id=obj.get("subscription"),
                current_period_start=now,
            )
            .on_conflict_do_update(
                index_elements=[tenant_subscriptions.c.tenant_id],
                set_={
                    "plan_code": plan_code,
                    "status": "ACTIVE",
                    "billing_provider": "STRIPE",
                    "provider_customer_id": obj.get("customer"),
                    "provider_subscription_id": obj.get("subscription"),
                    "current_period_start": now,
                    "updated_at": now,
                },
            )
        )
        await conn.commit()
    return {"received": True}


def _ticket(row: Any) -> SupportTicketResponse:
    return SupportTicketResponse(
        id=str(row.id),
        subject=row.subject,
        description=row.description,
        category=row.category,
        priority=row.priority,
        status=row.status,
        response_due_at=row.response_due_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/v1/support/tickets", response_model=list[SupportTicketResponse])
async def list_support_tickets(
    user: AuthenticatedUser = Depends(get_current_user),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> list[SupportTicketResponse]:
    rows = (
        await conn.execute(
            sa.select(support_tickets)
            .where(support_tickets.c.tenant_id == tenant_uuid(user))
            .order_by(support_tickets.c.created_at.desc())
        )
    ).all()
    return [_ticket(row) for row in rows]


@router.post("/v1/support/tickets", response_model=SupportTicketResponse, status_code=201)
async def create_support_ticket(
    body: SupportTicketRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> SupportTicketResponse:
    user_id = await ensure_workspace_user(conn, user)
    response_hours = 4 if body.priority.upper() == "URGENT" else 24
    row = (
        await conn.execute(
            support_tickets.insert()
            .values(
                id=uuid.uuid4(),
                tenant_id=tenant_uuid(user),
                created_by=user_id,
                subject=body.subject,
                description=body.description,
                category=body.category.upper(),
                priority=body.priority.upper(),
                response_due_at=datetime.now(timezone.utc) + timedelta(hours=response_hours),
            )
            .returning(support_tickets)
        )
    ).one()
    return _ticket(row)


@router.get("/v1/account-success/tasks", response_model=list[SuccessTaskResponse])
async def list_success_tasks(
    user: AuthenticatedUser = Depends(get_current_user),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> list[SuccessTaskResponse]:
    rows = (
        await conn.execute(
            sa.select(account_success_tasks)
            .where(account_success_tasks.c.tenant_id == tenant_uuid(user))
            .order_by(
                (account_success_tasks.c.status == "COMPLETED").asc(),
                account_success_tasks.c.due_at.asc().nulls_last(),
            )
        )
    ).all()
    return [
        SuccessTaskResponse(
            id=str(row.id),
            title=row.title,
            description=row.description,
            status=row.status,
            due_at=row.due_at,
            completed_at=row.completed_at,
        )
        for row in rows
    ]


@router.patch("/v1/account-success/tasks/{task_id}", response_model=SuccessTaskResponse)
async def complete_success_task(
    task_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> SuccessTaskResponse:
    now = datetime.now(timezone.utc)
    row = (
        await conn.execute(
            account_success_tasks.update()
            .where(
                account_success_tasks.c.id == task_id,
                account_success_tasks.c.tenant_id == tenant_uuid(user),
            )
            .values(status="COMPLETED", completed_at=now)
            .returning(account_success_tasks)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Onboarding task not found")
    return SuccessTaskResponse(
        id=str(row.id),
        title=row.title,
        description=row.description,
        status=row.status,
        due_at=row.due_at,
        completed_at=row.completed_at,
    )


@router.get("/v1/status", response_model=StatusResponse)
async def public_status(
    conn: AsyncConnection = Depends(get_conn),
) -> StatusResponse:
    incidents = (
        await conn.execute(
            sa.select(service_incidents)
            .where(service_incidents.c.status != "RESOLVED")
            .order_by(service_incidents.c.started_at.desc())
        )
    ).mappings().all()
    affected = {
        component
        for incident in incidents
        for component in (incident.get("affected_components") or [])
    }
    components = [
        {
            "name": name,
            "status": "DEGRADED" if name in affected else "OPERATIONAL",
        }
        for name in ("Web application", "API", "Data ingestion", "Search", "Alerts")
    ]
    return StatusResponse(
        status="DEGRADED" if incidents else "OPERATIONAL",
        generated_at=datetime.now(timezone.utc),
        components=components,
        incidents=[dict(item) for item in incidents],
    )


@router.get("/v1/help/articles")
async def help_articles() -> list[dict[str, Any]]:
    return [
        {
            "slug": "first-opportunities",
            "title": "Από το εταιρικό προφίλ στις πρώτες ευκαιρίες",
            "category": "Getting started",
            "steps": [
                "Περιγράψτε συγκεκριμένα προϊόντα, υπηρεσίες και γεωγραφική κάλυψη.",
                "Επιβεβαιώστε CPV και keywords πριν την εφαρμογή.",
                "Χρησιμοποιήστε feedback σχετικό/μη σχετικό για βελτίωση του scoring.",
            ],
        },
        {
            "slug": "bid-workspace",
            "title": "Qualification και παραγωγή προσφοράς",
            "category": "Bids",
            "steps": [
                "Ανοίξτε μια διαδικασία και δημιουργήστε bid workspace.",
                "Εξαγάγετε απαιτήσεις από τα επίσημα έγγραφα.",
                "Δημιουργήστε cited drafts και εγκρίνετε κάθε section πριν το Word export.",
            ],
        },
        {
            "slug": "data-confidence",
            "title": "Πώς διαβάζονται coverage και confidence",
            "category": "Data",
            "steps": [
                "Το coverage panel διαχωρίζει observed από verified coverage.",
                "Ανοίξτε την επίσημη πηγή ή το evidence drawer για κρίσιμες αποφάσεις.",
                "Μη χρησιμοποιείτε ceiling συμφωνίας-πλαίσιο ως πραγματοποιημένη δαπάνη.",
            ],
        },
    ]

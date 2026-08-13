"""On-demand provider fetch requests.

These endpoints make exact-identifier misses observable. They do not block
on provider calls; work is persisted and drained by the durable worker.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import fetch_requests
from packages.auth.jwt_verifier import AuthenticatedUser
from packages.schemas.responses import FetchRequestCreate, FetchRequestResponse
from services.ingestion.on_demand import ensure_fetch_request
from services.product.entitlements import EntitlementLimitExceeded, consume_entitlement

from ..auth import get_current_user
from ..db import get_tenant_scoped_conn
from ..workspace import tenant_uuid

router = APIRouter(prefix="/v1/fetch-requests", tags=["fetch-requests"])


def build_fetch_request_response(row) -> FetchRequestResponse:
    values = row._mapping
    return FetchRequestResponse(
        id=str(values["id"]),
        identifier_raw=values["identifier_raw"],
        identifier_normalized=values["identifier_normalized"],
        identifier_scheme=values["identifier_scheme"],
        source_system=values["source_system"],
        status=values["status"],
        message=values["message"],
        result_act_id=str(values["result_act_id"]) if values["result_act_id"] else None,
        result_process_id=str(values["result_process_id"]) if values["result_process_id"] else None,
        requested_at=values["requested_at"],
        started_at=values["started_at"],
        finished_at=values["finished_at"],
        last_attempt_at=values["last_attempt_at"],
        attempt_count=values["attempt_count"] or 0,
        next_retry_at=values["next_retry_at"],
        request_metadata=values["request_metadata"] or {},
    )


async def load_fetch_request(conn: AsyncConnection, request_id: uuid.UUID):
    row = (await conn.execute(select(fetch_requests).where(fetch_requests.c.id == request_id))).first()
    if row is None:
        raise HTTPException(status_code=404, detail="fetch request not found")
    return row


@router.post("", response_model=FetchRequestResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_fetch_request(
    body: FetchRequestCreate,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> FetchRequestResponse:
    try:
        await consume_entitlement(
            conn,
            tenant_id=tenant_uuid(user),
            metric_code="provider_fetches_month",
        )
    except EntitlementLimitExceeded as exc:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "ENTITLEMENT_LIMIT",
                "metric": exc.metric_code,
                "limit": exc.limit,
                "usage": exc.usage,
            },
        ) from exc
    request_id = await ensure_fetch_request(conn, body.identifier)
    if request_id is None:
        raise HTTPException(status_code=422, detail="Only exact ΑΔΑΜ/ΑΔΑ identifiers can be fetched on demand")

    row = await load_fetch_request(conn, request_id)
    return build_fetch_request_response(row)


@router.get("/{request_id}", response_model=FetchRequestResponse)
async def get_fetch_request(
    request_id: uuid.UUID,
    _: AuthenticatedUser = Depends(get_current_user),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> FetchRequestResponse:
    return build_fetch_request_response(await load_fetch_request(conn, request_id))

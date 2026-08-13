"""FastAPI application for the procurement intelligence platform.

    uvicorn apps.api.main:app --reload

Requires DATABASE_URL (see infra/docker/.env.example); importing this
module does not, so tests can build the OpenAPI schema without a DB.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import os
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager

import sqlalchemy as sa
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from .db import get_engine
from packages.domain.tables import audit_log, users
from packages.request_rate_limit import consume_request_quota, prune_request_quota

from .routers import (
    account,
    analytics,
    alert_rules,
    bids,
    bid_reports,
    business_profiles,
    buyers,
    companies,
    competitors,
    commercial,
    contracts,
    decision_makers,
    document_intelligence,
    document_tools,
    entity_review,
    europe,
    evidence,
    exports,
    fetch_requests,
    frameworks,
    intelligence,
    onboarding,
    processes,
    proposals,
    search,
    search_fulltext,
    signals,
    workspace,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Warm operational aggregates before accepting the first user request."""
    if os.environ.get("DATABASE_URL"):
        try:
            async with get_engine().connect() as conn:
                await analytics.data_coverage(conn)
        except Exception:
            logger.exception("Could not warm the data coverage cache")
    yield


_production = os.environ.get("PROCINTEL_ENV", "development").lower() == "production"
app = FastAPI(
    title="Procurement Intelligence API",
    version="0.1.0",
    description="Greek Public Procurement Intelligence platform with cross-source procurement enrichment.",
    docs_url=None if _production else "/docs",
    redoc_url=None if _production else "/redoc",
    lifespan=lifespan,
)

_cors_origins = [
    value.strip()
    for value in os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    ).split(",")
    if value.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=[
        value.strip()
        for value in os.environ.get("TRUSTED_HOSTS", "*").split(",")
        if value.strip()
    ],
)

_request_count = 0
_request_errors = 0
_request_duration_seconds = 0.0
_rate_windows: dict[str, deque[float]] = defaultdict(deque)
_rate_lock = asyncio.Lock()


def _client_ip(request: Request) -> str:
    environment = os.environ.get("PROCINTEL_ENV", "development").casefold()
    internal_client = request.headers.get("x-procintel-client-ip", "").strip()
    if environment in {"production", "staging"} and internal_client:
        try:
            return str(ipaddress.ip_address(internal_client))
        except ValueError:
            pass
    return request.client.host if request.client else "unknown"


def _request_rate_keys(request: Request) -> tuple[str, ...]:
    client_host = _client_ip(request)
    keys = [f"ip:{client_host}"]
    authorization = request.headers.get("authorization", "")
    if authorization:
        keys.append("credential:" + hashlib.sha256(authorization.encode("utf-8")).hexdigest())
    return tuple(keys)


def _prune_rate_windows(cutoff: float, *, max_keys: int) -> None:
    expired = [key for key, window in _rate_windows.items() if not window or window[-1] <= cutoff]
    for key in expired:
        _rate_windows.pop(key, None)
    if len(_rate_windows) > max_keys:
        oldest = sorted(_rate_windows, key=lambda key: _rate_windows[key][-1])
        for key in oldest[: len(_rate_windows) - max_keys]:
            _rate_windows.pop(key, None)


@app.middleware("http")
async def enforce_request_rate_limit(request: Request, call_next):
    if request.url.path in {"/health", "/health/ready", "/metrics"}:
        return await call_next(request)
    limit = max(1, int(os.environ.get("PROCINTEL_RATE_LIMIT_PER_MINUTE", "600")))
    keys = _request_rate_keys(request)
    environment = os.environ.get("PROCINTEL_ENV", "development").casefold()
    if environment in {"production", "staging"}:
        try:
            async with get_engine().begin() as conn:
                decision = await consume_request_quota(conn, keys, limit=limit)
                if uuid.uuid4().int % 1000 == 0:
                    await prune_request_quota(conn)
        except Exception:  # noqa: BLE001 - production limiter fails closed
            logger.exception("Distributed request limiter is unavailable")
            return JSONResponse(status_code=503, content={"detail": "Request throttling is unavailable"})
        remaining = decision.remaining
        retry_after = decision.retry_after
        blocked = not decision.allowed
    else:
        now = time.monotonic()
        cutoff = now - 60
        max_keys = max(100, int(os.environ.get("PROCINTEL_RATE_LIMIT_MAX_KEYS", "10000")))
        async with _rate_lock:
            _prune_rate_windows(cutoff, max_keys=max_keys)
            effective_keys = []
            for key in keys:
                effective_key = key if key in _rate_windows or len(_rate_windows) < max_keys else "overflow"
                if effective_key not in effective_keys:
                    effective_keys.append(effective_key)
                window = _rate_windows[effective_key]
                while window and window[0] <= cutoff:
                    window.popleft()
            windows = [_rate_windows[key] for key in effective_keys]
            blocked_window = next((window for window in windows if len(window) >= limit), None)
            retry_after = max(1, int(60 - (now - blocked_window[0]))) if blocked_window else 0
            blocked = blocked_window is not None
            if not blocked:
                for window in windows:
                    window.append(now)
            remaining = max(0, limit - max(len(window) for window in windows))
    if blocked:
        return JSONResponse(
            status_code=429,
            content={"detail": "Request rate limit exceeded"},
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
            },
        )
    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    return response


def _audit_required(request: Request) -> bool:
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        return True
    return any(
        request.url.path.startswith(prefix)
        for prefix in (
            "/v1/account",
            "/v1/entity-review",
            "/v1/intelligence/funding-links/review",
            "/v1/exports",
        )
    )


async def _record_request_audit(
    request: Request,
    *,
    request_id: str,
    response_status: int,
) -> None:
    user = getattr(request.state, "auth_user", None)
    if user is None or not _audit_required(request):
        return
    tenant_id = uuid.UUID(user.tenant_id) if user.tenant_id else None
    route = request.scope.get("route")
    route_path = getattr(route, "path", request.url.path)
    object_id = None
    for value in request.path_params.values():
        try:
            object_id = uuid.UUID(str(value))
            break
        except ValueError:
            continue
    try:
        async with get_engine().begin() as conn:
            if tenant_id:
                await conn.execute(
                    sa.text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(tenant_id)},
                )
            actor_user_id = None
            if user.email:
                actor_user_id = (
                    await conn.execute(
                        sa.select(users.c.id).where(users.c.email == user.email)
                    )
                ).scalar_one_or_none()
            await conn.execute(
                audit_log.insert().values(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    actor_user_id=actor_user_id,
                    action=(
                        "admin.access"
                        if request.method == "GET"
                        else f"http.{request.method.casefold()}"
                    ),
                    object_type=route_path,
                    object_id=object_id,
                    details={
                        "path": request.url.path,
                        "query": str(request.url.query),
                        "status_code": response_status,
                        "subject": user.subject,
                        "role": user.role,
                        "auth_method": user.auth_method,
                    },
                    ip_address=_client_ip(request),
                    request_id=request_id,
                    outcome="SUCCESS" if response_status < 400 else "DENIED",
                )
            )
    except Exception:
        # Audit failures are reflected in metrics without replacing an already
        # completed business response; production monitoring alerts on 5xx/DB.
        global _request_errors
        _request_errors += 1


@app.middleware("http")
async def record_request_metrics(request: Request, call_next):
    global _request_count, _request_errors, _request_duration_seconds
    started = time.perf_counter()
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    response = await call_next(request)
    _request_count += 1
    _request_duration_seconds += time.perf_counter() - started
    if response.status_code >= 500:
        _request_errors += 1
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Request-ID"] = request_id
    await _record_request_audit(
        request,
        request_id=request_id,
        response_status=response.status_code,
    )
    return response

app.include_router(contracts.router)
app.include_router(account.router)
app.include_router(document_intelligence.router)
app.include_router(processes.router)
app.include_router(search.router)
app.include_router(search_fulltext.router)
app.include_router(signals.router)
app.include_router(fetch_requests.router)
app.include_router(buyers.router)
app.include_router(companies.router)
app.include_router(alert_rules.router)
app.include_router(bids.router)
app.include_router(bid_reports.router)
app.include_router(business_profiles.router)
app.include_router(workspace.router)
app.include_router(evidence.router)
app.include_router(entity_review.router)
app.include_router(intelligence.router)
app.include_router(onboarding.router)
app.include_router(exports.router)
app.include_router(analytics.router)
app.include_router(competitors.router)
app.include_router(competitors.process_router)
app.include_router(decision_makers.router)
app.include_router(frameworks.router)
app.include_router(proposals.router)
app.include_router(commercial.router)
app.include_router(document_tools.router)
app.include_router(europe.router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def readiness() -> dict[str, str]:
    try:
        async with get_engine().connect() as conn:
            await conn.execute(sa.text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - readiness must map any dependency failure to 503
        return Response(
            content=f'{{"status":"unavailable","database":"{type(exc).__name__}"}}',
            status_code=503,
            media_type="application/json",
        )
    return {"status": "ready", "database": "ok"}


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    lines = [
        "# HELP procintel_api_requests_total Total API requests.",
        "# TYPE procintel_api_requests_total counter",
        f"procintel_api_requests_total {_request_count}",
        "# HELP procintel_api_errors_total Total API responses with status 5xx.",
        "# TYPE procintel_api_errors_total counter",
        f"procintel_api_errors_total {_request_errors}",
        "# HELP procintel_api_request_duration_seconds_sum Accumulated request duration.",
        "# TYPE procintel_api_request_duration_seconds_sum counter",
        f"procintel_api_request_duration_seconds_sum {_request_duration_seconds:.6f}",
    ]
    try:
        async with get_engine().connect() as conn:
            source_rows = (
                await conn.execute(
                    sa.text(
                        """
                        SELECT source_system,
                               EXTRACT(EPOCH FROM (now() - MAX(fetched_at))) AS age_seconds
                        FROM source_records
                        GROUP BY source_system
                        """
                    )
                )
            ).all()
            for row in source_rows:
                source = str(row.source_system).replace('"', "")
                lines.append(
                    f'procintel_source_freshness_seconds{{source="{source}"}} '
                    f"{float(row.age_seconds or 0):.3f}"
                )
            queue_depth = (
                await conn.execute(
                    sa.text(
                        """
                        SELECT COUNT(*) FROM entity_match_candidates
                        WHERE status = 'PENDING_REVIEW'
                        """
                    )
                )
            ).scalar_one()
            lines.append(f"procintel_entity_review_queue_depth {int(queue_depth)}")
    except Exception:  # noqa: BLE001 - API process metrics remain available during DB incidents
        lines.append("procintel_database_available 0")
    else:
        lines.append("procintel_database_available 1")
    return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

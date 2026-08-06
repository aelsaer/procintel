"""Drain durable cross-source enrichment jobs within provider budgets."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import enrichment_jobs, procurement_acts
from packages.source_clients.rate_limit import TokenBucket
from packages.source_clients.raw_store import LocalFilesystemRawStore
from services.documents.download import DocumentTooLargeError
from services.documents.pdf_text import (
    PdfPageLimitExceededError,
    PdfPageTooLargeError,
)
from services.documents.pipeline import (
    UnsupportedMimeTypeError,
    VirusDetectedError,
)
from services.ingestion.connectors.anaptyxi.client import AnaptyxiClient
from services.ingestion.connectors.anaptyxi.config import (
    SUPPORTED_PROGRAM_PERIODS,
    AnaptyxiConnectorConfig,
    AnaptyxiUpstreamContractError,
)
from services.ingestion.connectors.anaptyxi.resolve import resolve_funding_link_for_act
from services.ingestion.connectors.diavgeia.client import DiavgeiaClient
from services.ingestion.connectors.diavgeia.config import DiavgeiaConnectorConfig
from services.ingestion.connectors.diavgeia.resolve import (
    resolve_decision_for_ada,
    resolve_decision_via_search,
)
from services.ingestion.connectors.gemi.client import GemiClient
from services.ingestion.connectors.gemi.config import GemiConnectorConfig
from services.ingestion.connectors.gemi.provider import GemiCompanyRegistryProvider
from services.ingestion.connectors.gemi.resolve import resolve_company_snapshot
from services.ingestion.connectors.khmdhs.adamchain import (
    get_act_id_by_adam,
    resolve_adam_chain_for_act,
)
from services.ingestion.connectors.khmdhs.client import KhmdhsClient
from services.ingestion.connectors.khmdhs.config import KhmdhsConnectorConfig
from services.ingestion.connectors.khmdhs.afm import valid_greek_afm
from services.ingestion.connectors.khmdhs.documents import process_khmdhs_attachment
from services.ingestion.connectors.khmdhs.scheduled import (
    _fetch_act_details_for_anaptyxi,
)
from services.ingestion.connectors.mef.client import MefClient, MefUpstreamUnavailableError
from services.ingestion.connectors.mef.config import MefConnectorConfig
from services.ingestion.connectors.mef.resolve import resolve_expenses_for_contractor
from services.ingestion.enrichment_queue import (
    ClaimedEnrichmentJob,
    claim_enrichment_jobs,
    complete_enrichment,
    fail_enrichment,
    recover_stale_enrichment_jobs,
)
from services.search_index.config import OpenSearchConfig
from services.search_index.indexer import index_single_act


class ProviderConfigurationError(RuntimeError):
    pass


class ProviderUpstreamContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class EnrichmentSweepResult:
    claimed: int
    succeeded: int
    failed: int
    blocked_config: int
    blocked_upstream: int
    deferred: int
    by_provider: dict[str, dict[str, int]]


class _Dependencies:
    def __init__(self, raw_root: str) -> None:
        self.raw_store = LocalFilesystemRawStore(raw_root)
        khmdhs_config = KhmdhsConnectorConfig.from_env()
        self.khmdhs_rate_limiter = TokenBucket(
            khmdhs_config.rate_limit_per_minute
        )
        self.khmdhs = KhmdhsClient(
            khmdhs_config,
            rate_limiter=self.khmdhs_rate_limiter,
        )
        self.diavgeia = DiavgeiaClient(DiavgeiaConnectorConfig.from_env())
        self.mef = MefClient(MefConnectorConfig.from_env())
        self.gemi_client: GemiClient | None = None
        self.gemi_provider: GemiCompanyRegistryProvider | None = None
        self.anaptyxi: dict[str, AnaptyxiClient] = {}
        self.config_errors: dict[str, str] = {}
        self.upstream_errors: dict[str, str] = {}
        self.runtime_unavailable_providers: set[str] = set()
        try:
            self.gemi_client = GemiClient(GemiConnectorConfig.from_env())
            self.gemi_provider = GemiCompanyRegistryProvider(self.gemi_client)
        except RuntimeError as exc:
            self.config_errors["GEMI"] = str(exc)
        for period in SUPPORTED_PROGRAM_PERIODS:
            try:
                self.anaptyxi[period] = AnaptyxiClient(
                    AnaptyxiConnectorConfig.from_env(program_period=period)
                )
            except AnaptyxiUpstreamContractError as exc:
                self.upstream_errors[period] = str(exc)
            except RuntimeError as exc:
                self.config_errors[period] = str(exc)
        try:
            self.opensearch_config: OpenSearchConfig | None = (
                OpenSearchConfig.from_env()
            )
        except RuntimeError as exc:
            self.opensearch_config = None
            self.config_errors["OPENSEARCH"] = str(exc)
        self.http = httpx.AsyncClient(timeout=60.0)

    async def aclose(self) -> None:
        await self.khmdhs.aclose()
        await self.diavgeia.aclose()
        await self.mef.aclose()
        if self.gemi_client is not None:
            await self.gemi_client.aclose()
        for client in self.anaptyxi.values():
            await client.aclose()
        await self.http.aclose()

    @property
    def available_providers(self) -> set[str]:
        providers = {
            "KHMDHS_DOCUMENT",
            "KHMDHS_ADAMCHAIN",
            "DIAVGEIA",
            "DIAVGEIA_SEARCH",
            "MEF",
        }
        if self.gemi_provider is not None:
            providers.add("GEMI")
        providers.update(self.anaptyxi)
        if self.opensearch_config is not None:
            providers.add("OPENSEARCH")
        return providers.difference(self.runtime_unavailable_providers)

    @property
    def known_providers(self) -> set[str]:
        return {
            "KHMDHS_DOCUMENT",
            "KHMDHS_ADAMCHAIN",
            "DIAVGEIA",
            "DIAVGEIA_SEARCH",
            "GEMI",
            "MEF",
            "OPENSEARCH",
            *SUPPORTED_PROGRAM_PERIODS,
        }


def _uuid(payload: dict[str, Any], key: str) -> uuid.UUID:
    value = payload.get(key)
    if not value:
        raise ValueError(f"enrichment payload is missing {key}")
    return uuid.UUID(str(value))


async def _dispatch(
    conn: AsyncConnection,
    dependencies: _Dependencies,
    job: ClaimedEnrichmentJob,
) -> dict[str, Any]:
    payload = job.payload
    provider = job.provider
    if provider == "KHMDHS_DOCUMENT":
        result = await process_khmdhs_attachment(
            conn,
            resource=str(payload["resource"]),
            adam=str(payload["adam"]),
            act_id=_uuid(payload, "act_id"),
            http_client=dependencies.http,
            rate_limiter=dependencies.khmdhs_rate_limiter,
        )
        return {"document_processed": result is not None}
    if provider == "KHMDHS_ADAMCHAIN":
        act_id = await get_act_id_by_adam(conn, str(payload["adam"]))
        if act_id is not None:
            process_id = (
                await conn.execute(
                    sa.select(procurement_acts.c.process_id).where(
                        procurement_acts.c.id == act_id
                    )
                )
            ).scalar()
            if process_id is not None:
                return {
                    "process_id": str(process_id),
                    "already_resolved": True,
                    "_external_call": False,
                }
        process_id = await resolve_adam_chain_for_act(
            conn,
            client=dependencies.khmdhs,
            raw_store=dependencies.raw_store,
            seed_adam_normalized=str(payload["adam"]),
        )
        return {"process_id": str(process_id) if process_id else None}
    if provider == "DIAVGEIA":
        act_id = await resolve_decision_for_ada(
            conn,
            client=dependencies.diavgeia,
            raw_store=dependencies.raw_store,
            ada=str(payload["ada"]),
            origin_act_id=_uuid(payload, "origin_act_id"),
            process_documents=True,
        )
        return {"decision_act_id": str(act_id) if act_id else None}
    if provider == "DIAVGEIA_SEARCH":
        process_id_raw = payload.get("process_id")
        if process_id_raw:
            has_existing_link = (
                await conn.execute(
                    sa.text(
                        """
                        SELECT EXISTS (
                          SELECT 1
                          FROM procurement_acts origin
                          JOIN act_links link ON link.to_act_id = origin.id
                          JOIN procurement_acts decision
                            ON decision.id = link.from_act_id
                           AND decision.act_type = 'DIAVGEIA_DECISION'
                          WHERE origin.process_id = :process_id
                        )
                        """
                    ),
                    {"process_id": uuid.UUID(str(process_id_raw))},
                )
            ).scalar()
            if has_existing_link:
                return {
                    "already_resolved": True,
                    "_external_call": False,
                }
        act_id = await resolve_decision_via_search(
            conn,
            client=dependencies.diavgeia,
            raw_store=dependencies.raw_store,
            origin_act_id=_uuid(payload, "origin_act_id"),
            organization_query=str(payload["organization_query"]),
            title_query=str(payload["title_query"]),
            process_documents=True,
        )
        return {"decision_act_id": str(act_id) if act_id else None}
    if provider == "GEMI":
        if dependencies.gemi_provider is None:
            raise ProviderConfigurationError(
                dependencies.config_errors.get("GEMI", "GEMI is not configured")
            )
        afm = str(payload["afm"])
        if not valid_greek_afm(afm):
            return {"skipped_invalid_afm": True, "_external_call": False}
        result = await resolve_company_snapshot(
            conn,
            provider=dependencies.gemi_provider,
            raw_store=dependencies.raw_store,
            afm_normalized=afm,
            entity_id=_uuid(payload, "entity_id"),
        )
        return {"wrote_new_snapshot": result.wrote_new_snapshot}
    if provider in SUPPORTED_PROGRAM_PERIODS:
        client = dependencies.anaptyxi.get(provider)
        if client is None:
            if provider in dependencies.upstream_errors:
                raise ProviderUpstreamContractError(
                    dependencies.upstream_errors[provider]
                )
            raise ProviderConfigurationError(
                dependencies.config_errors.get(
                    provider, f"{provider} is not configured"
                )
            )
        act_id = _uuid(payload, "act_id")
        details = await _fetch_act_details_for_anaptyxi(conn, act_id)
        project_id = await resolve_funding_link_for_act(
            conn,
            client=client,
            raw_store=dependencies.raw_store,
            act_id=act_id,
            mis_candidates=[
                (str(item[0]), str(item[1]))
                for item in payload.get("funding_ref_candidates", [])
                if isinstance(item, list) and len(item) == 2
            ],
            beneficiary_afm=details["buyer_afm"],
            contractor_afm=payload.get("contractor_afm"),
            act_title=details["title"],
            act_date=details["date"],
            related_ada_candidates=[
                str(value) for value in payload.get("related_ada", [])
            ],
            act_amount=details["amount"],
            act_region=details["region"],
        )
        return {"funding_project_id": str(project_id) if project_id else None}
    if provider == "MEF":
        afm = str(payload["afm"])
        if not valid_greek_afm(afm):
            return {"skipped_invalid_afm": True, "_external_call": False}
        try:
            count = await resolve_expenses_for_contractor(
                conn,
                client=dependencies.mef,
                raw_store=dependencies.raw_store,
                contractor_entity_id=_uuid(payload, "entity_id"),
                afm_normalized=afm,
            )
        except MefUpstreamUnavailableError as exc:
            dependencies.runtime_unavailable_providers.add("MEF")
            raise ProviderUpstreamContractError(str(exc)) from exc
        return {"expenses_ingested": count}
    if provider == "OPENSEARCH":
        if dependencies.opensearch_config is None:
            raise ProviderConfigurationError(
                dependencies.config_errors.get(
                    "OPENSEARCH", "OpenSearch is not configured"
                )
            )
        await index_single_act(
            conn,
            dependencies.http,
            dependencies.opensearch_config,
            _uuid(payload, "act_id"),
        )
        return {"indexed": True}
    raise ProviderConfigurationError(f"unsupported enrichment provider {provider}")


def _increment(
    by_provider: dict[str, dict[str, int]], provider: str, status: str
) -> None:
    counts = by_provider.setdefault(provider, {})
    counts[status] = counts.get(status, 0) + 1


async def run_pending_enrichment_jobs(
    conn: AsyncConnection,
    *,
    raw_root: str,
    limit: int = 500,
    provider_budgets: dict[str, int] | None = None,
    providers: set[str] | None = None,
) -> EnrichmentSweepResult:
    dependencies = _Dependencies(raw_root)
    if dependencies.upstream_errors:
        await conn.execute(
            enrichment_jobs.update()
            .where(
                enrichment_jobs.c.provider.in_(dependencies.upstream_errors),
                enrichment_jobs.c.status == "BLOCKED_CONFIG",
            )
            .values(status="BLOCKED_UPSTREAM")
        )
        await conn.commit()
    await recover_stale_enrichment_jobs(conn)
    claimed = 0
    succeeded = failed = blocked = upstream_blocked = deferred = 0
    attempts: dict[str, int] = {}
    by_provider: dict[str, dict[str, int]] = {}
    try:
        for _ in range(limit):
            eligible_providers = {
                provider
                for provider in dependencies.known_providers
                if providers is None or provider in providers
                if provider not in dependencies.runtime_unavailable_providers
                if (provider_budgets or {}).get(provider) is None
                or attempts.get(provider, 0)
                < int((provider_budgets or {})[provider])
            }
            if not eligible_providers:
                break
            jobs = await claim_enrichment_jobs(
                conn,
                limit=1,
                providers=eligible_providers,
                reactivate_blocked_providers=dependencies.available_providers,
            )
            if not jobs:
                break
            job = jobs[0]
            claimed += 1
            try:
                result = await _dispatch(conn, dependencies, job)
            except ProviderConfigurationError as exc:
                attempts[job.provider] = attempts.get(job.provider, 0) + 1
                await conn.rollback()
                await fail_enrichment(
                    conn,
                    job.id,
                    error={"type": type(exc).__name__, "message": str(exc)},
                    blocked_config=True,
                )
                await conn.commit()
                blocked += 1
                _increment(by_provider, job.provider, "blocked_config")
            except ProviderUpstreamContractError as exc:
                attempts[job.provider] = attempts.get(job.provider, 0) + 1
                dependencies.runtime_unavailable_providers.add(job.provider)
                await conn.rollback()
                await fail_enrichment(
                    conn,
                    job.id,
                    error={"type": type(exc).__name__, "message": str(exc)},
                    blocked_upstream=True,
                )
                await conn.commit()
                upstream_blocked += 1
                _increment(by_provider, job.provider, "blocked_upstream")
            except (
                DocumentTooLargeError,
                PdfPageLimitExceededError,
                PdfPageTooLargeError,
                UnsupportedMimeTypeError,
                VirusDetectedError,
            ) as exc:
                attempts[job.provider] = attempts.get(job.provider, 0) + 1
                await conn.rollback()
                await fail_enrichment(
                    conn,
                    job.id,
                    error={"type": type(exc).__name__, "message": str(exc)},
                    permanent=True,
                )
                await conn.commit()
                failed += 1
                _increment(by_provider, job.provider, "dead")
            except Exception as exc:  # noqa: BLE001 - one provider job is one failure boundary
                attempts[job.provider] = attempts.get(job.provider, 0) + 1
                await conn.rollback()
                await fail_enrichment(
                    conn,
                    job.id,
                    error={"type": type(exc).__name__, "message": str(exc)},
                )
                await conn.commit()
                failed += 1
                _increment(by_provider, job.provider, "failed")
            else:
                external_call = bool(result.pop("_external_call", True))
                if external_call:
                    attempts[job.provider] = attempts.get(job.provider, 0) + 1
                await complete_enrichment(conn, job.id, result=result)
                await conn.commit()
                succeeded += 1
                _increment(by_provider, job.provider, "succeeded")

        pending_rows = (
            await conn.execute(
                sa.select(
                    enrichment_jobs.c.provider,
                    sa.func.count().label("count"),
                )
                .where(
                    enrichment_jobs.c.provider.in_(
                        dependencies.known_providers
                        if providers is None
                        else dependencies.known_providers.intersection(providers)
                    ),
                    enrichment_jobs.c.status.in_(("QUEUED", "FAILED")),
                )
                .group_by(enrichment_jobs.c.provider)
            )
        ).all()
        for row in pending_rows:
            count = int(row.count)
            deferred += count
            by_provider.setdefault(row.provider, {})["deferred"] = count
    finally:
        await dependencies.aclose()
    return EnrichmentSweepResult(
        claimed=claimed,
        succeeded=succeeded,
        failed=failed,
        blocked_config=blocked,
        blocked_upstream=upstream_blocked,
        deferred=deferred,
        by_provider=by_provider,
    )

"""`CompanyRegistryProvider` — description.txt §18.4's exact interface.

Enrichment logic (`resolve.py`) is built against this Protocol, not against
`GemiClient` directly, so the MVP doesn't functionally depend on the ΓΕΜΗ
API key request being approved (§18.4) — a different provider (a stub, a
different registry, a manually-curated fallback) can stand in later without
touching `resolve.py`. `GemiCompanyRegistryProvider` is the only
implementation right now, backed by `GemiClient`.

Deviates from the spec's literal pseudocode in one place: `find_by_vat`/
`find_by_gemi` return `NormalizedCompany | None` rather than an unconditional
`CompanyResult` — a lookup that can't find anything is a real, expected
outcome (§18.3's "αρνητικό αποτέλεσμα" / negative result), not an error, and
`None` is the honest way to represent that.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from .client import CompanyNotFoundError, GemiClient, GemiCompanyResponse
from .normalize import NormalizedCompany, normalize_company_record


@dataclass(frozen=True)
class CompanySearchQuery:
    name: str | None = None
    kad: str | None = None
    status: str | None = None
    prefecture: str | None = None
    municipality: str | None = None


@dataclass(frozen=True)
class ProviderLookupResult:
    company: NormalizedCompany | None
    raw_response: GemiCompanyResponse | None  # None when the lookup found nothing


class CompanyRegistryProvider(Protocol):
    async def find_by_vat(self, country: str, vat: str) -> ProviderLookupResult: ...

    async def find_by_gemi(self, gemi_number: str) -> ProviderLookupResult: ...

    async def search(self, query: CompanySearchQuery) -> list[NormalizedCompany]: ...


class GemiCompanyRegistryProvider:
    def __init__(self, client: GemiClient) -> None:
        self._client = client

    async def _include_public_documents(self, response: GemiCompanyResponse) -> GemiCompanyResponse:
        gemi_number = response.body.get("arGemi")
        if not gemi_number:
            return response
        try:
            documents = await self._client.get_company_documents(str(gemi_number))
        except CompanyNotFoundError:
            return response
        merged_body = {**response.body, "publicDocuments": documents.body}
        return GemiCompanyResponse(
            query=response.query,
            body=merged_body,
            raw_body=json.dumps(merged_body, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            http_status=response.http_status,
        )

    async def find_by_vat(self, country: str, vat: str) -> ProviderLookupResult:
        if country != "GR":
            # ΓΕΜΗ is a Greek national registry — foreign VAT lookups belong
            # to VIES (§3.9), not here.
            return ProviderLookupResult(company=None, raw_response=None)
        try:
            response = await self._client.find_by_vat(vat)
        except CompanyNotFoundError:
            return ProviderLookupResult(company=None, raw_response=None)
        response = await self._include_public_documents(response)
        return ProviderLookupResult(
            company=normalize_company_record(response.body, afm=vat), raw_response=response
        )

    async def find_by_gemi(self, gemi_number: str) -> ProviderLookupResult:
        try:
            response = await self._client.find_by_gemi_number(gemi_number)
        except CompanyNotFoundError:
            return ProviderLookupResult(company=None, raw_response=None)
        response = await self._include_public_documents(response)
        afm = response.body.get("afm", "")
        return ProviderLookupResult(
            company=normalize_company_record(response.body, afm=afm), raw_response=response
        )

    async def search(self, query: CompanySearchQuery) -> list[NormalizedCompany]:
        """Attribute search — not used by the ΑΦΜ-triggered enrichment flow
        (`resolve.py` always starts from a known ΑΦΜ), but a real capability
        for other callers (e.g. a future entity-resolution disambiguation
        step, or a manual lookup UI). No results is a legitimate empty
        list, not an error."""
        params: dict[str, str] = {}
        if query.name:
            params["name"] = query.name
        if query.kad:
            params["activities"] = query.kad
        if query.status:
            params["statuses"] = query.status
        if query.prefecture:
            params["prefectures"] = query.prefecture
        if query.municipality:
            params["municipalities"] = query.municipality

        response = await self._client.search(params)
        return [
            normalize_company_record(raw, afm=raw.get("afm", "")) for raw in response.results
        ]

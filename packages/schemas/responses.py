"""API response models — description.txt §30.4's shape (identifiers, buyer,
suppliers, amounts, data_quality, provenance blocks), applied at whichever
granularity each endpoint actually operates at (single act for
`/v1/contracts/{adam}`, whole process for `/v1/processes/{id}`).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class PartyResponse(BaseModel):
    id: str
    name: str
    vat: str | None = None
    amount: Decimal | None = None


class AmountsResponse(BaseModel):
    net: Decimal | None = None
    vat: Decimal | None = None
    gross: Decimal | None = None
    currency: str = "EUR"


class ProvenanceEntry(BaseModel):
    source: str
    source_native_id: str | None = None
    retrieved_at: datetime | None = None


class TenderSummaryKeyPoint(BaseModel):
    label: str
    value: str
    source: str


class TenderSummaryResponse(BaseModel):
    text: str
    key_points: list[TenderSummaryKeyPoint] = Field(default_factory=list)
    document_excerpt: str | None = None
    methodology: str = "STRUCTURED_EXTRACTIVE"
    primary_act_id: str | None = None


class OfficialRecordResponse(BaseModel):
    act_id: str
    act_type: str
    title: str | None = None
    source_system: str
    resource_type: str | None = None
    identifier_scheme: str | None = None
    identifier: str | None = None
    event_date: date | datetime | None = None
    official_url: str | None = None
    document_url: str | None = None


class TenderDocumentResponse(BaseModel):
    document_id: str
    act_id: str | None = None
    document_type: str
    title: str | None = None
    source_url: str | None = None
    object_uri: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    text_extraction_status: str
    page_count: int | None = None
    language: str | None = None
    excerpt: str | None = None


class ContractResponse(BaseModel):
    id: str
    process_id: str | None = None
    act_type: str
    title: str | None = None
    status: str | None = None
    procedure_type: str | None = None
    identifiers: dict[str, list[str]] = {}
    buyer: PartyResponse | None = None
    suppliers: list[PartyResponse] = []
    amounts: AmountsResponse
    provenance: list[ProvenanceEntry] = []
    summary: TenderSummaryResponse
    official_records: list[OfficialRecordResponse] = Field(default_factory=list)
    documents: list[TenderDocumentResponse] = Field(default_factory=list)


class TimelineNode(BaseModel):
    act_id: str
    act_type: str
    title: str | None = None
    event_date: date | None = None
    amount_gross: Decimal | None = None
    status: str | None = None
    identifiers: dict[str, list[str]] = {}


class ProcessTimelineResponse(BaseModel):
    process_id: str
    nodes: list[TimelineNode]


class ProcessDetailResponse(BaseModel):
    """Mirrors db/marts/procurement_360.sql column-for-column — this
    endpoint is a thin pass-through over that view, not a re-derivation."""

    process_id: str
    public_id: str
    title: str | None = None
    lifecycle_status: str
    record_status: str
    estimated_value: Decimal | None = None
    awarded_value: Decimal | None = None
    current_contract_value: Decimal | None = None
    currency: str = "EUR"
    buyer: dict[str, Any]
    suppliers: list[dict[str, Any]] = []
    supplier_company_info: list[dict[str, Any]] = []
    acts: list[dict[str, Any]] = []
    lots: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    diavgeia_decisions: list[dict[str, Any]] = []
    ted_notices: list[dict[str, Any]] = []
    funding_projects: list[dict[str, Any]] = []
    mef_expense_signals: list[dict[str, Any]] = []
    locations: list[dict[str, Any]] = []
    data_quality: dict[str, Any]
    summary: TenderSummaryResponse
    official_records: list[OfficialRecordResponse] = Field(default_factory=list)
    first_observed_at: datetime | None = None
    last_observed_at: datetime | None = None


class SearchResultItem(BaseModel):
    act_id: str
    process_id: str | None = None
    adam: str | None = None
    identifier_scheme: str | None = None
    identifier_value: str | None = None
    title: str | None = None
    act_type: str
    match_type: str
    relevance: float | None = None
    buyer_name: str | None = None
    cpv_codes: list[str] = Field(default_factory=list)
    event_date: date | None = None
    official_url: str | None = None
    document_url: str | None = None


class FetchRequestCreate(BaseModel):
    identifier: str


class FetchRequestResponse(BaseModel):
    id: str
    identifier_raw: str
    identifier_normalized: str
    identifier_scheme: str
    source_system: str
    status: str
    message: str | None = None
    result_act_id: str | None = None
    result_process_id: str | None = None
    requested_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_attempt_at: datetime | None = None
    attempt_count: int = 0
    next_retry_at: datetime | None = None
    request_metadata: dict[str, Any] = {}


class PaginationBlock(BaseModel):
    next_cursor: str | None = None
    has_more: bool = False


class SearchResponse(BaseModel):
    data: list[SearchResultItem]
    pagination: PaginationBlock
    fetch_request: FetchRequestResponse | None = None


class BuyerSummaryResponse(BaseModel):
    id: str
    name: str
    vat: str | None = None
    total_contract_value: Decimal | None = None
    contract_count: int


class SupplierShareResponse(BaseModel):
    id: str
    name: str
    vat: str | None = None
    value: Decimal | None = None
    contract_count: int


class BuyerSuppliersResponse(BaseModel):
    buyer_id: str
    suppliers: list[SupplierShareResponse]


class CompanySummaryResponse(BaseModel):
    id: str
    name: str
    vat: str | None = None
    legal_form: str | None = None
    company_status: str | None = None
    total_public_sector_value: Decimal | None = None
    contract_count: int


class CompanyContractsResponse(BaseModel):
    company_id: str
    contracts: list[ContractResponse]

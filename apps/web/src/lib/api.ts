/**
 * Typed client for apps/api (FastAPI) — description.txt §30.1/§30.4's
 * response shapes, mirrored from the FastAPI response models. Keeping this
 * client explicit also preserves the UI's domain-specific query helpers.
 */

import { activeCpvPrefixes, activeKeywords, workspaceFilterQuery, type BusinessScope } from "@/lib/business-scope";
import { getValidAccessToken } from "@/lib/oidc";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  const headers = new Headers(init?.headers);
  if (typeof window !== "undefined") {
    const token = await getValidAccessToken();
    if (token && !headers.has("Authorization")) headers.set("Authorization", `Bearer ${token}`);
  }
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(
      0,
      `Δεν υπάρχει σύνδεση με το API (${API_BASE_URL}). Ελέγξτε ότι τρέχει το FastAPI service στο localhost:8000.`
    );
  }

  if (!response.ok) {
    const body = await response.text();
    let message = body || response.statusText;

    try {
      const parsed = JSON.parse(body) as { detail?: unknown; message?: unknown };
      const detail = parsed.detail ?? parsed.message;
      if (typeof detail === "string") {
        message = detail;
      }
    } catch {
      // The API is allowed to return plain text errors.
    }

    if (response.status >= 500 && (body.trim().startsWith("<") || body.trim() === "Internal Server Error")) {
      message = `Το API proxy δεν πήρε έγκυρη απάντηση από το backend (${response.status}). Ελέγξτε το FastAPI service στο localhost:8000.`;
    }

    throw new ApiError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function downloadApiFile(path: string, fallbackName: string): Promise<void> {
  const headers = new Headers();
  const token = await getValidAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${API_BASE_URL}${path}`, { headers });
  if (!response.ok) {
    throw new ApiError(response.status, (await response.text()) || response.statusText);
  }
  const disposition = response.headers.get("content-disposition") ?? "";
  const match = disposition.match(/filename="([^"]+)"/i);
  const fileName = match?.[1] ?? fallbackName;
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export interface PartyResponse {
  id: string;
  name: string;
  vat: string | null;
  amount: number | null;
}

export interface AmountsResponse {
  net: number | null;
  vat: number | null;
  gross: number | null;
  currency: string;
}

export interface ProvenanceEntry {
  source: string;
  source_native_id: string | null;
  retrieved_at: string | null;
}

export interface TenderSummaryKeyPoint {
  label: string;
  value: string;
  source: string;
}

export interface TenderSummary {
  text: string;
  key_points: TenderSummaryKeyPoint[];
  document_excerpt: string | null;
  methodology: "STRUCTURED_EXTRACTIVE" | string;
  primary_act_id: string | null;
}

export interface OfficialRecord {
  act_id: string;
  act_type: string;
  title: string | null;
  source_system: string;
  resource_type: string | null;
  identifier_scheme: string | null;
  identifier: string | null;
  event_date: string | null;
  official_url: string | null;
  document_url: string | null;
}

export interface TenderDocument {
  document_id: string;
  act_id: string | null;
  document_type: string;
  title: string | null;
  source_url: string | null;
  object_uri: string | null;
  mime_type: string | null;
  file_size: number | null;
  text_extraction_status: string;
  page_count: number | null;
  language: string | null;
  excerpt: string | null;
}

export interface ContractResponse {
  id: string;
  process_id: string | null;
  act_type: string;
  title: string | null;
  status: string | null;
  procedure_type: string | null;
  identifiers: Record<string, string[]>;
  buyer: PartyResponse | null;
  suppliers: PartyResponse[];
  amounts: AmountsResponse;
  provenance: ProvenanceEntry[];
  summary: TenderSummary;
  official_records: OfficialRecord[];
  documents: TenderDocument[];
}

export interface TimelineNode {
  act_id: string;
  act_type: string;
  title: string | null;
  event_date: string | null;
  amount_gross: number | null;
  status: string | null;
  identifiers: Record<string, string[]>;
}

export interface ProcessTimelineResponse {
  process_id: string;
  nodes: TimelineNode[];
}

export interface BidTask {
  id: string;
  title: string;
  description: string | null;
  assigned_user_id: string | null;
  status: "TODO" | "IN_PROGRESS" | "BLOCKED" | "DONE";
  priority: "LOW" | "MEDIUM" | "HIGH" | "URGENT";
  due_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface BidRequirement {
  id: string;
  requirement_type:
    | "ELIGIBILITY"
    | "TECHNICAL"
    | "FINANCIAL"
    | "CERTIFICATE"
    | "DELIVERABLE"
    | "DEADLINE"
    | "LEGAL"
    | "OTHER";
  title: string;
  description: string | null;
  status: "UNREVIEWED" | "MET" | "PARTIAL" | "MISSING" | "NOT_APPLICABLE";
  mandatory: boolean;
  evidence_document_id: string | null;
  evidence_page: number | null;
  source_excerpt: string | null;
  owner_user_id: string | null;
  due_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface BidWorkspace {
  id: string;
  process_id: string;
  process_title: string | null;
  owner_user_id: string | null;
  status: "QUALIFYING" | "PREPARING" | "REVIEW" | "SUBMITTED" | "WON" | "LOST" | "ARCHIVED";
  decision: "PENDING" | "BID" | "NO_BID" | "CONDITIONAL";
  decision_rationale: string | null;
  decision_by: string | null;
  decision_at: string | null;
  submission_due_at: string | null;
  created_at: string;
  updated_at: string;
  tasks: BidTask[];
  requirements: BidRequirement[];
  comments: BidComment[];
  reminders: BidReminder[];
  certificates: BidCertificateLink[];
  crm_handoffs: CrmHandoff[];
  activity: Array<Record<string, unknown>>;
}

export interface BidReportResponse {
  process_id: string;
  public_id: string;
  title: string;
  generated_at: string;
  recommendation: "BID" | "NO_BID" | "CONDITIONAL";
  confidence: number;
  recommendation_reasons: string[];
  opportunity_score: number;
  data_confidence: number;
  buyer_name: string | null;
  budget: number | string | null;
  currency: string;
  deadline: string | null;
  geography: string[];
  fit: Record<string, number>;
  risks: Array<Record<string, unknown>>;
  mandatory_requirements: Array<Record<string, unknown>>;
  missing_certificates: Array<Record<string, unknown>>;
  competitors: Array<Record<string, unknown>>;
  evidence: Array<Record<string, unknown>>;
  next_actions: Array<{ type: string; priority: string; label: string }>;
  limitations: string[];
}

export interface BidContentResponse {
  id: string;
  title: string;
  content_type: string;
  body: string;
  tags: string[];
  approved: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProposalSectionResponse {
  id: string;
  requirement_id: string | null;
  section_key: string;
  title: string;
  display_order: number;
  status: "DRAFT" | "IN_REVIEW" | "APPROVED" | "NEEDS_CHANGES";
  current_version: number;
  body: string;
  citations: Array<Record<string, unknown>>;
  generation_metadata: Record<string, unknown>;
  assigned_user_id: string | null;
  updated_at: string;
}

export interface ProposalWorkspaceResponse {
  process_id: string;
  bid_workspace_id: string;
  process_title: string | null;
  sections: ProposalSectionResponse[];
  requirements_total: number;
  requirements_mapped: number;
  approved_sections: number;
}

export interface ProposalVersionResponse {
  id: string;
  version_number: number;
  body: string;
  citations: Array<Record<string, unknown>>;
  change_summary: string | null;
  created_by: string;
  created_at: string;
}

export interface BidComment {
  id: string;
  task_id: string | null;
  requirement_id: string | null;
  author_user_id: string;
  author_name?: string | null;
  author_email?: string | null;
  body: string;
  created_at: string;
  updated_at: string;
}

export interface BidReminder {
  id: string;
  task_id: string | null;
  requirement_id: string | null;
  assigned_user_id: string | null;
  remind_at: string;
  channel: "IN_APP" | "EMAIL" | "WEBHOOK";
  status: "PENDING" | "SENT" | "DISMISSED" | "FAILED";
  sent_at: string | null;
}

export interface TenantCertificate {
  id: string;
  title: string;
  certificate_type: string;
  issuer: string | null;
  reference_number: string | null;
  file_name: string | null;
  storage_uri: string | null;
  issued_at: string | null;
  expires_at: string | null;
}

export interface BidCertificateLink extends TenantCertificate {
  link_id: string;
  requirement_id: string | null;
  linked_at: string;
}

export interface CrmHandoff {
  id: string;
  provider: string;
  external_reference: string | null;
  status: "PENDING" | "SYNCED" | "FAILED";
  error_message: string | null;
  created_at: string;
  synced_at: string | null;
}

export interface DocumentCitation {
  document_id: string;
  document_title: string | null;
  source_url: string | null;
  page: number;
  excerpt: string;
  rank: number;
}

export interface DocumentAnswer {
  answer: string;
  mode: "LLM_GROUNDED" | "EXTRACTIVE" | "NO_EVIDENCE";
  citations: DocumentCitation[];
  limitations: string;
}

// procurement_360.sql's own jsonb_build_object() column shapes — mirrored
// by hand, same as everything else in this file.
export interface ProcessBuyer {
  entity_id: string | null;
  name: string | null;
  vat: string | null;
  aaht: string | null;
}

export interface ProcessSupplier {
  entity_id: string;
  party_role: string;
  name: string;
  amount: number | null;
  currency: string | null;
  lot_id: string | null;
}

export interface ProcessAct {
  act_id: string;
  act_type: string;
  title: string | null;
  procedure_type: string | null;
  agreement_type: string;
  framework_ceiling_amount: number | null;
  publication_date: string | null;
  submission_date: string | null;
  submission_deadline: string | null;
  decision_date: string | null;
  start_date: string | null;
  end_date: string | null;
  amount_net: number | null;
  vat_amount: number | null;
  amount_gross: number | null;
  currency: string | null;
  status: string | null;
  is_current: boolean;
  identifiers: Record<string, string[]>;
  source_record_id: string | null;
}

export interface ProcessDetailResponse {
  process_id: string;
  public_id: string;
  title: string | null;
  lifecycle_status: string;
  record_status: string;
  estimated_value: number | null;
  awarded_value: number | null;
  current_contract_value: number | null;
  currency: string;
  buyer: ProcessBuyer;
  suppliers: ProcessSupplier[];
  supplier_company_info: Record<string, unknown>[];
  acts: ProcessAct[];
  lots: Record<string, unknown>[];
  documents: TenderDocument[];
  diavgeia_decisions: Record<string, unknown>[];
  ted_notices: Record<string, unknown>[];
  funding_projects: Record<string, unknown>[];
  mef_expense_signals: Record<string, unknown>[];
  locations: Record<string, unknown>[];
  data_quality: Record<string, unknown>;
  summary: TenderSummary;
  official_records: OfficialRecord[];
  first_observed_at: string | null;
  last_observed_at: string | null;
}

export interface SearchResultItem {
  act_id: string;
  process_id: string | null;
  adam: string | null;
  identifier_scheme: "ADAM" | "ADA" | null;
  identifier_value: string | null;
  title: string | null;
  act_type: string;
  match_type: "EXACT_IDENTIFIER" | "EXACT_ENTITY_IDENTIFIER" | "EXACT_PHRASE" | "TITLE_TERMS";
  relevance: number | null;
  buyer_name: string | null;
  cpv_codes: string[];
  event_date: string | null;
  official_url: string | null;
  document_url: string | null;
}

export interface PaginationBlock {
  next_cursor: string | null;
  has_more: boolean;
}

export interface SearchResponse {
  data: SearchResultItem[];
  pagination: PaginationBlock;
  fetch_request: FetchRequestResponse | null;
}

export interface FetchRequestResponse {
  id: string;
  identifier_raw: string;
  identifier_normalized: string;
  identifier_scheme: "ADAM" | "ADA";
  source_system: "KHMDHS" | "DIAVGEIA";
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "NOT_FOUND" | "WAITING_FOR_CONFIG" | "FAILED";
  message: string | null;
  result_act_id: string | null;
  result_process_id: string | null;
  requested_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  last_attempt_at: string | null;
  attempt_count: number;
  next_retry_at: string | null;
  request_metadata: {
    provider_rate_policy?: {
      rate_limit_per_minute?: number | null;
      official_ceiling_per_minute?: number | null;
      window_days?: number | null;
      notes?: string;
    };
    [key: string]: unknown;
  };
}

export interface FulltextHit {
  id: string;
  title: string | null;
  act_type: string;
  score: number;
  buyer_name: string | null;
  amount_gross: number | null;
  cpv_codes: string[];
}

export interface FulltextSearchResponse {
  total: number;
  data: FulltextHit[];
}

export interface BuyerSummaryResponse {
  id: string;
  name: string;
  vat: string | null;
  total_contract_value: number | null;
  contract_count: number;
}

export interface SupplierShareResponse {
  id: string;
  name: string;
  vat: string | null;
  value: number | null;
  contract_count: number;
}

export interface BuyerSuppliersResponse {
  buyer_id: string;
  suppliers: SupplierShareResponse[];
}

export interface DecisionMakerInvolvement {
  official_identifier: string | null;
  event_date: string | null;
  official_url: string | null;
}

export interface DecisionMakerResponse {
  id: string;
  buyer_entity_id: string;
  full_name: string;
  job_title: string | null;
  department: string | null;
  decision_role: string;
  email: string | null;
  phone: string | null;
  profile_url: string | null;
  source_system: string;
  source_url: string | null;
  legal_basis: string;
  confidence: number;
  is_current: boolean;
  observed_at: string;
  watched: boolean;
  watch_id: string | null;
  watch_notes: string | null;
  recent_involvement: DecisionMakerInvolvement[];
}

export interface DecisionMakerListResponse {
  buyer_id: string;
  stakeholders: DecisionMakerResponse[];
  methodology: string;
}

export interface FrameworkSupplierResponse {
  entity_id: string;
  name: string;
  lot_identifier: string | null;
  awarded_value: number | string | null;
  membership_status: string;
}

export interface FrameworkResponse {
  act_id: string;
  process_id: string | null;
  public_id: string | null;
  title: string;
  buyer_id: string | null;
  buyer_name: string | null;
  cpv_codes: string[];
  status: "ACTIVE" | "REOPENING" | "EXPIRED" | "UNKNOWN";
  publication_date: string | null;
  valid_from: string | null;
  valid_until: string | null;
  days_to_expiry: number | null;
  ceiling_amount: number | string | null;
  realized_spend: number | string;
  utilization: number | null;
  call_off_count: number;
  suppliers: FrameworkSupplierResponse[];
  buyer_count: number;
  relevance_score: number;
  official_identifier: string | null;
  official_url: string | null;
  watched: boolean;
  watch_id: string | null;
  notify_before_days: number | null;
}

export interface FrameworkListResponse {
  generated_at: string;
  summary: Record<string, number | string | null>;
  frameworks: FrameworkResponse[];
  methodology: string[];
}

export interface EuropeanBenchmarkResponse {
  country_code: string;
  country_name: string;
  cpv_prefix: string;
  notice_count: number;
  award_count: number;
  total_value: number | string;
  median_value: number | string | null;
  valued_notice_count: number;
  deadline_notice_count: number;
  average_parse_confidence: number | null;
}

export interface EuropeanBenchmarkListResponse {
  generated_at: string;
  date_from: string;
  date_to: string;
  cpv_prefixes: string[];
  covered_countries: number;
  rows: EuropeanBenchmarkResponse[];
  methodology: string[];
}

export interface CrossBorderOpportunityResponse {
  act_id: string;
  process_id: string | null;
  ted_notice_id: string;
  publication_number: string | null;
  official_url: string;
  title: string;
  buyer_name: string | null;
  country_code: string;
  country_name: string;
  cpv_codes: string[];
  estimated_value: number | string | null;
  currency: string;
  publication_date: string | null;
  submission_deadline: string | null;
  match_score: number | string;
  reasons: string[];
  barriers: string[];
  parse_confidence: number;
  computed_at: string;
}

export interface CrossBorderOpportunityListResponse {
  generated_at: string;
  profile_version: number;
  candidates_seen: number;
  matches: CrossBorderOpportunityResponse[];
  methodology: string[];
}

export interface CompanySummaryResponse {
  id: string;
  name: string;
  vat: string | null;
  legal_form: string | null;
  company_status: string | null;
  total_public_sector_value: number | null;
  contract_count: number;
}

export interface CompanyContractsResponse {
  company_id: string;
  contracts: ContractResponse[];
}

export interface TopSupplierResponse {
  supplier_id: string;
  supplier_name: string;
  afm: string | null;
  recorded_value: number | null;
  act_count: number;
}

export interface TopBuyerResponse {
  buyer_id: string;
  buyer_name: string;
  vat: string | null;
  recorded_value: number | null;
  act_count: number;
  supplier_count: number;
}

export interface RegionActivityResponse {
  act_id: string;
  process_id: string | null;
  title: string | null;
  act_type: string;
  status: string | null;
  buyer_name: string | null;
  amount_gross: number | string | null;
  event_date: string | null;
  cpv_codes: string[];
  nuts_codes: string[];
  location_labels: string[];
}

export interface RenewalWatchResponse {
  contract_act_id: string;
  process_id: string;
  end_date: string;
  days_to_end: number;
  avg_lead_time_days: number | null;
  renewal_watch_active: boolean;
  title: string | null;
  buyer_name: string | null;
  supplier_name: string | null;
}

export interface RiskIndicatorResponse {
  indicator_type: string;
  message: string;
  subject: Record<string, unknown>;
  value: unknown;
  benchmark: unknown;
  minimum_sample: number;
  sample_size: number;
  confidence: "HIGH" | "MEDIUM" | "LOW";
  sources: string[];
  calculated_at: string;
  limitations: string;
  definition: string;
}

export interface MarketOverviewResponse {
  process_count: number;
  act_count: number;
  opportunity_count: number;
  contract_count: number;
  notice_count: number;
  payment_count: number;
  recorded_contract_value: number | null;
  acts_with_geo: number;
  acts_with_precise_geo: number;
}

export interface RegionAnalyticsResponse {
  nuts_code: string;
  region_name: string;
  act_count: number;
  opportunity_count: number;
  notice_count: number;
  contract_count: number;
  recorded_contract_value: number | string | null;
}

export interface OpportunityResponse {
  act_id: string;
  process_id: string | null;
  title: string | null;
  act_type: string;
  buyer_name: string | null;
  amount_gross: number | string | null;
  event_date: string | null;
  submission_date: string | null;
  cpv_codes: string[];
  nuts_codes: string[];
  location_labels: string[];
  fit_score: number;
  evidence: string[];
}

export interface ProcurementSignalResponse {
  id: string;
  signal_type: "PROCUREMENT_PLAN" | "CONSULTATION" | "BUDGET_APPROVAL" | "COMMITTEE_MINUTES" | "EXPIRING_CONTRACT" | "EARLY_REQUEST";
  title: string;
  description: string | null;
  buyer_name: string | null;
  source_url: string | null;
  source_identifier: string | null;
  publication_date: string | null;
  expected_notice_date: string | null;
  estimated_value: number | string | null;
  currency: string;
  cpv_codes: string[];
  nuts_codes: string[];
  confidence: number;
  evidence: Record<string, unknown>;
  process_id: string | null;
}

export interface GeocodedLocationAnalyticsResponse {
  label: string;
  nuts_code: string | null;
  municipality_name: string | null;
  regional_unit_name: string | null;
  region_name: string | null;
  latitude: number;
  longitude: number;
  act_count: number;
  opportunity_count: number;
  contract_count: number;
  recorded_contract_value: number | string | null;
  minimum_confidence: number | null;
}

export interface SourceResourceCoverage {
  resource_type: string;
  record_count: number;
  parsed_count: number;
  failed_count: number;
}

export interface SourceCoverage {
  source_system: string;
  record_count: number;
  parsed_count: number;
  failed_count: number;
  latest_fetched_at: string | null;
  resources: SourceResourceCoverage[];
}

export interface DataConnectionCoverage {
  source: string;
  target: string;
  relation: string;
  available_records: number;
  linked_records: number;
  status: "CONNECTED" | "LOADED_UNLINKED" | "NOT_LOADED";
}

export interface ConnectorRunCoverage {
  source_system: string;
  resource_type: string;
  partition_key: string;
  status: string;
  records_fetched: number;
  records_upserted: number;
  records_unchanged: number;
  records_failed: number;
  enrichment_succeeded: number;
  enrichment_failed: number;
  enrichment_deferred: number;
  started_at: string;
  finished_at: string | null;
  error: Record<string, unknown> | null;
}

export interface DataCoverageResponse {
  generated_at: string;
  totals: Record<string, number>;
  sources: SourceCoverage[];
  connections: DataConnectionCoverage[];
  recent_runs: ConnectorRunCoverage[];
  dataset_validations: Array<{
    dataset_name: string;
    adapter_name: string;
    resource_url: string;
    detected_format: string | null;
    columns: string[];
    status: string;
    errors: unknown[];
    validated_at: string;
  }>;
  assessments: SourceCompletenessResponse[];
}

export interface SourceCompletenessResponse {
  source_system: string;
  status: "HEALTHY" | "DEGRADED" | "PARTIAL" | "STALE" | "UNAVAILABLE";
  score: number;
  claim_level: "VERIFIED_WINDOW" | "OBSERVED_COVERAGE";
  expected_basis: string;
  observed_records: number;
  expected_records: number | null;
  canonical_records: number;
  records_with_documents: number;
  records_with_parties: number;
  records_with_locations: number;
  failed_records: number;
  pending_enrichments: number;
  freshness_seconds: number | null;
  freshness_target_seconds: number;
  dimensions: Record<string, number | null>;
  findings: Array<{ code: string; severity: string; message: string }>;
}

export interface CompetitorSummary {
  company_id: string;
  name: string;
  afm: string | null;
  gemi_number: string | null;
  company_status: string | null;
  classification: "CONFIRMED_BIDDER" | "MARKET_COMPETITOR" | "CONFIRMED_WINNER";
  evidence_level: "CONFIRMED_PARTICIPATION" | "INFERRED_FROM_AWARDS" | "OFFICIAL_AWARD";
  similarity_score: number;
  score_evidence: string[];
  award_count: number;
  bid_count: number;
  recorded_value: number | string | null;
  buyer_count: number;
  shared_buyer_count: number;
  head_to_head_count: number;
  cpv_codes: string[];
  nuts_codes: string[];
  last_activity: string | null;
}

export interface CompetitorDiscoveryResponse {
  competitors: CompetitorSummary[];
  coverage: {
    processes_analyzed: number;
    companies_found: number;
    confirmed_bidder_facts: number;
    confirmed_winner_facts: number;
    source_note: string;
  };
  scope: {
    cpv_prefixes: string[];
    keywords: string[];
    nuts_code: string | null;
    municipality: string | null;
    date_from: string | null;
    date_to: string | null;
    amount_min: number | string | null;
    reference_afm: string | null;
    taxonomy_match: "ANY";
  };
}

export interface CompetitorBreakdown {
  key: string;
  label: string;
  count: number;
  recorded_value: number | string | null;
}

export interface CompetitorActivity {
  activity_id: string;
  process_id: string;
  public_id: string;
  title: string | null;
  role: string;
  evidence_level: string;
  event_date: string | null;
  value: number | string | null;
  buyer_name: string | null;
}

export interface CompetitorProfileResponse {
  company_id: string;
  name: string;
  afm: string | null;
  gemi_number: string | null;
  company_status: string | null;
  legal_form: string | null;
  metrics: {
    award_count: number;
    bid_count: number;
    recorded_value: number | string | null;
    buyer_count: number;
    head_to_head_count: number;
  };
  top_buyers: CompetitorBreakdown[];
  cpv_distribution: CompetitorBreakdown[];
  regions: CompetitorBreakdown[];
  recent_activity: CompetitorActivity[];
}

export interface ProcessParticipant {
  company_id: string | null;
  name: string;
  afm: string | null;
  role: string;
  classification: "CONFIRMED_PARTICIPANT" | "CONFIRMED_WINNER" | "INFERRED_MARKET_COMPETITOR";
  confidence: number;
  evidence_type: "OFFICIAL_SOURCE" | "DOCUMENT_EXTRACTED" | "MARKET_INFERENCE";
  evidence_label: string;
  document_id: string | null;
  source_page: number | null;
}

export interface ProcessCompetitionResponse {
  process_id: string;
  confirmed_participants: ProcessParticipant[];
  likely_incumbent: ProcessParticipant | null;
  likely_competitors: ProcessParticipant[];
  coverage_note: string;
}

export interface ProfileTermResponse {
  id: string | null;
  term_type: "CPV_PREFIX" | "KEYWORD" | string;
  value: string;
  label: string;
  confidence: number;
  reason: string;
  source: string;
  is_active: boolean;
}

export interface BusinessProfileResponse {
  id: string;
  company_name: string | null;
  description: string;
  cpv_prefixes: string[];
  keywords: string[];
  excluded_cpv_prefixes: string[];
  excluded_keywords: string[];
  nuts_codes: string[];
  municipality: string | null;
  buyer_types: string[];
  procedure_types: string[];
  amount_min: number | string | null;
  amount_max: number | string | null;
  classification_version: number;
  classified_at: string | null;
  updated_at: string;
  terms: ProfileTermResponse[];
}

export interface OnboardingStatusResponse {
  required: boolean;
  session_id: string | null;
  status: string;
  current_step: string;
  description: string;
  selected_cpv_codes: string[];
  selected_keywords: string[];
  selected_nuts_codes: string[];
  quality_score: number | null;
  quality_findings: Array<Record<string, unknown>>;
}

export interface OnboardingSuggestionResponse {
  session_id: string;
  cpv_suggestions: ProfileTermResponse[];
  keyword_suggestions: ProfileTermResponse[];
}

export interface InitialOpportunityResponse {
  process_id: string;
  title: string;
  buyer_name: string | null;
  amount: number | string | null;
  deadline: string | null;
  score: number;
  data_confidence: number;
  cpv_codes: string[];
  locations: string[];
  adam: string | null;
  official_url: string | null;
  document_url: string | null;
  reasons: Array<Record<string, unknown>>;
}

export interface OnboardingCompleteResponse {
  session_id: string;
  profile_id: string;
  quality_score: number;
  quality_findings: Array<Record<string, unknown>>;
  review_status: string | null;
  opportunities: InitialOpportunityResponse[];
}

export interface OpportunityScoringStatusResponse {
  status: "IDLE" | "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED";
  reason: string | null;
  requested_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  error: Record<string, unknown> | null;
}

export interface RelevanceFeedbackResponse {
  id: string;
  process_id: string;
  label: "RELEVANT" | "IRRELEVANT";
  reason: string | null;
  updated_at: string;
}

export interface AccountMember {
  id: string;
  user_id: string;
  email: string;
  display_name: string | null;
  role: "OWNER" | "ADMIN" | "ANALYST" | "SALES" | "BID_MANAGER" | "VIEWER";
  mfa_enabled: boolean;
  joined_at: string;
}

export interface AccountInvitation {
  id: string;
  email: string;
  role: AccountMember["role"];
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
  created_at: string;
  invitation_token: string | null;
}

export interface AccountApiKey {
  id: string;
  name: string;
  key_prefix: string;
  scopes: string[];
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
  key: string | null;
}

export interface WorkspaceMeResponse {
  subject: string;
  email: string | null;
  tenant_id: string;
  tenant_name: string;
  plan: string;
  role: string;
}

export interface PipelineItemResponse {
  id: string;
  process_id: string;
  process_title: string | null;
  stage: string;
  priority: string;
  expected_value: number | string | null;
  next_action: string | null;
  due_at: string | null;
  opportunity_score: number | string | null;
  added_at: string;
  updated_at: string;
}

export interface SavedSearchResponse {
  id: string;
  name: string;
  query: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface WatchResponse {
  id: string;
  object_type: "BUYER" | "COMPETITOR" | "SUPPLIER";
  object_id: string;
  object_name: string | null;
  created_at: string;
}

export interface NoteResponse {
  id: string;
  object_type: string;
  object_id: string;
  body: string;
  created_at: string;
  updated_at: string;
}

export interface TagResponse {
  id: string;
  name: string;
  linked_count: number;
}

export interface AlertTargetResponse {
  id: string;
  channel_type: string;
  target: string;
  is_active: boolean;
}

export interface AlertRuleResponse {
  id: string;
  name: string;
  event_types: string[];
  filters: Record<string, unknown>;
  schedule: string;
  delivery_channels: string[];
  timezone: string;
  digest_time: string;
  is_active: boolean;
  targets: AlertTargetResponse[];
  event_count: number;
  unread_count: number;
  created_at: string;
  updated_at: string;
}

export interface AlertEventResponse {
  id: string;
  alert_rule_id: string;
  rule_name: string;
  canonical_object_type: string;
  canonical_object_id: string;
  event_type: string;
  payload: Record<string, unknown>;
  triggered_at: string;
  delivered_at: string | null;
  read_at: string | null;
}

export interface AlertDigestHistoryResponse {
  id: string;
  alert_rule_id: string | null;
  schedule: string;
  event_count: number;
  status: string;
  channels: string[];
  created_at: string;
  delivered_at: string | null;
}

export interface AlertDeliveryHistoryResponse {
  id: string;
  alert_event_id: string;
  channel: string;
  status: string;
  attempt_count: number;
  response_status: number | null;
  last_attempt_at: string | null;
  next_retry_at: string | null;
  created_at: string;
}

export interface OpportunityIntelligenceResponse {
  process_id: string;
  title: string | null;
  buyer_id: string | null;
  buyer_name: string | null;
  amount: number | string | null;
  deadline: string | null;
  adam: string | null;
  official_url: string | null;
  document_url: string | null;
  cpv_codes: string[];
  locations: string[];
  score: number | string | null;
  score_breakdown: Record<string, number | string | null>;
  evidence: Array<Record<string, unknown>>;
  pipeline_stage: string | null;
}

export interface SimilarContractResponse {
  process_id: string;
  title: string | null;
  buyer_name: string | null;
  contract_value: number | null;
  decision_date: string | null;
  cpv_codes: string[];
  similarity_score: number;
  reasons: string[];
}

export interface MarketDashboardResponse {
  summary: Record<string, number | string | null>;
  concentration: Record<string, number | string | null>;
  modifications: Record<string, number | string | null>;
  cycle_time: Record<string, number | string | null>;
  payment_execution: Record<string, number | string | null>;
  signals: Record<string, number | string | null>;
  procedure_mix: Array<Record<string, number | string | null>>;
  supplier_trends: Array<Record<string, number | string | null>>;
  methodologies: string[];
}

export interface EntityMatchCandidateResponse {
  id: string;
  entity_a: { id: string; name: string; entity_type: string; status: string };
  entity_b: { id: string; name: string; entity_type: string; status: string };
  score: number;
  score_breakdown: Record<string, unknown>;
  blocking_reason: string;
  status: string;
  review_notes: string | null;
  created_at: string;
}

export interface EntityMergeHistoryResponse {
  id: string;
  surviving_entity_id: string;
  merged_entity_id: string;
  merge_reason: string;
  evidence: Record<string, unknown>;
  performed_by: string;
  performed_at: string;
  reverted_at: string | null;
  reverted_by: string | null;
}

export interface AssistantResponse {
  answer: string;
  intent: string;
  data: Record<string, unknown>[];
  visualization: Record<string, unknown>;
  methodology: string;
}

export interface MarketMetricIntelligenceResponse {
  cpv_prefix: string;
  nuts_code: string | null;
  period_year: number;
  procedure_type: string | null;
  contract_count: number;
  total_value: number | string | null;
  average_value: number | string | null;
  median_value: number | string | null;
  supplier_count: number;
  buyer_count: number;
  hhi: number | string | null;
  value_basis: string;
  refreshed_at: string | null;
}

export interface ExportJobResponse {
  id: string;
  export_type: string;
  format: string;
  filters: Record<string, unknown>;
  status: string;
  row_count: number | null;
  file_name: string | null;
  error: Record<string, unknown> | null;
  created_at: string;
  finished_at: string | null;
  expires_at: string | null;
  download_url: string | null;
}

export interface CommercialPlan {
  code: string;
  name: string;
  description: string;
  monthly_price_cents: number | null;
  annual_price_cents: number | null;
  currency: string;
  trial_days: number;
  entitlements: Record<string, number | boolean>;
}

export interface SubscriptionResponse {
  plan: CommercialPlan;
  status: string;
  billing_provider: string;
  trial_ends_at: string | null;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  entitlements: Record<string, number | boolean>;
  usage: Record<string, number>;
}

export interface SupportTicketResponse {
  id: string;
  subject: string;
  description: string;
  category: string;
  priority: string;
  status: string;
  response_due_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface SuccessTaskResponse {
  id: string;
  title: string;
  description: string | null;
  status: string;
  due_at: string | null;
  completed_at: string | null;
}

export interface PublicStatusResponse {
  status: string;
  generated_at: string;
  components: Array<{ name: string; status: string }>;
  incidents: Array<Record<string, unknown>>;
}

export interface SectorProfileResponse {
  code: string;
  name: string;
  description: string;
  cpv_prefixes: string[];
  keywords: string[];
  excluded_keywords: string[];
  recommended_alerts: Array<Record<string, unknown>>;
}

export interface PhraseMatchResponse {
  id: string;
  document_id: string;
  process_id: string | null;
  document_title: string | null;
  process_title: string | null;
  matched_phrases: string[];
  page_numbers: number[];
  excerpts: Array<Record<string, unknown>>;
  matched_at: string;
}

export interface PhraseMonitorResponse {
  id: string;
  name: string;
  phrases: string[];
  match_mode: "ANY" | "ALL" | "EXACT";
  cpv_prefixes: string[];
  is_active: boolean;
  match_count: number;
  matches: PhraseMatchResponse[];
  created_at: string;
  updated_at: string;
}

export const api = {
  search: (q: string, cursor?: string, limit?: number, autoFetch = false) =>
    apiFetch<SearchResponse>(
      `/v1/search?q=${encodeURIComponent(q)}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}${
        limit ? `&limit=${encodeURIComponent(limit)}` : ""
      }&auto_fetch=${autoFetch ? "true" : "false"}`
    ),
  searchFulltext: (q: string) => apiFetch<FulltextSearchResponse>(`/v1/search/fulltext?q=${encodeURIComponent(q)}`),
  createFetchRequest: (identifier: string) =>
    apiFetch<FetchRequestResponse>("/v1/fetch-requests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ identifier }),
    }),
  getFetchRequest: (id: string) => apiFetch<FetchRequestResponse>(`/v1/fetch-requests/${encodeURIComponent(id)}`),
  getContract: (identifier: string) => apiFetch<ContractResponse>(`/v1/contracts/${encodeURIComponent(identifier)}`),
  getProcess: (id: string) => apiFetch<ProcessDetailResponse>(`/v1/processes/${encodeURIComponent(id)}`),
  getProcessTimeline: (id: string) =>
    apiFetch<ProcessTimelineResponse>(`/v1/processes/${encodeURIComponent(id)}/timeline`),
  getProcessCompetition: (id: string) =>
    apiFetch<ProcessCompetitionResponse>(`/v1/processes/${encodeURIComponent(id)}/competition`),
  getBidReport: (id: string) =>
    apiFetch<BidReportResponse>(`/v1/bid-reports/${encodeURIComponent(id)}`),
  getSimilarContracts: (id: string) =>
    apiFetch<SimilarContractResponse[]>(`/v1/processes/${encodeURIComponent(id)}/similar-contracts`),
  getBuyer: (id: string) => apiFetch<BuyerSummaryResponse>(`/v1/buyers/${encodeURIComponent(id)}`),
  getBuyerSuppliers: (id: string) =>
    apiFetch<BuyerSuppliersResponse>(`/v1/buyers/${encodeURIComponent(id)}/suppliers`),
  getBuyerDecisionMakers: (id: string) =>
    apiFetch<DecisionMakerListResponse>(`/v1/buyers/${encodeURIComponent(id)}/decision-makers`),
  watchDecisionMaker: (id: string, notes?: string) =>
    apiFetch<DecisionMakerResponse>(`/v1/decision-makers/${encodeURIComponent(id)}/watch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notes: notes || null }),
    }),
  unwatchDecisionMaker: (id: string) =>
    apiFetch<void>(`/v1/decision-makers/${encodeURIComponent(id)}/watch`, { method: "DELETE" }),
  watchFramework: (id: string, notifyBeforeDays = 90) =>
    apiFetch<{ framework_act_id: string; notify_before_days: number }>(
      `/v1/frameworks/${encodeURIComponent(id)}/watch`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ notify_before_days: notifyBeforeDays }),
      }
    ),
  unwatchFramework: (id: string) =>
    apiFetch<void>(`/v1/frameworks/${encodeURIComponent(id)}/watch`, { method: "DELETE" }),
  getCompany: (id: string) => apiFetch<CompanySummaryResponse>(`/v1/companies/${encodeURIComponent(id)}`),
  getCompanyContracts: (id: string) =>
    apiFetch<CompanyContractsResponse>(`/v1/companies/${encodeURIComponent(id)}/contracts`),
  getMarketOverview: (query: string) => apiFetch<MarketOverviewResponse>(`/v1/analytics/market-overview?${query}`),
  getRegionAnalytics: (query: string) => apiFetch<RegionAnalyticsResponse[]>(`/v1/analytics/regions?${query}`),
  getTopSuppliers: (query: string) => apiFetch<TopSupplierResponse[]>(`/v1/analytics/top-suppliers?${query}`),
  getTopBuyers: (query: string) => apiFetch<TopBuyerResponse[]>(`/v1/analytics/top-buyers?${query}`),
  getRegionActivity: (query: string) => apiFetch<RegionActivityResponse[]>(`/v1/analytics/region-activity?${query}`),
  getRenewalWatch: (limit = 10) => apiFetch<RenewalWatchResponse[]>(`/v1/intelligence/renewals?active_only=true&limit=${limit}`),
  getRiskIndicators: () => apiFetch<RiskIndicatorResponse[]>("/v1/intelligence/risk-indicators"),
  getOpportunities: (query: string) => apiFetch<OpportunityResponse[]>(`/v1/analytics/opportunities?${query}`),
  discoverCompetitors: (query: string) => apiFetch<CompetitorDiscoveryResponse>(`/v1/competitors/discover?${query}`),
  getCompetitor: (id: string, query = "") =>
    apiFetch<CompetitorProfileResponse>(`/v1/competitors/${encodeURIComponent(id)}${query ? `?${query}` : ""}`),
  getMe: () => apiFetch<WorkspaceMeResponse>("/v1/workspace/me"),
  acknowledgeLogin: () => apiFetch<{ acknowledged: boolean }>("/v1/workspace/login", { method: "POST" }),
  getBusinessProfile: () => apiFetch<BusinessProfileResponse>("/v1/business-profile"),
  classifyBusinessProfile: (description: string) => apiFetch<ProfileTermResponse[]>("/v1/business-profile/classify", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ description }),
  }),
  getBusinessProfileScoringStatus: () => apiFetch<OpportunityScoringStatusResponse>("/v1/business-profile/scoring-status"),
  updateBusinessProfile: (body: Record<string, unknown>) => apiFetch<BusinessProfileResponse>("/v1/business-profile", {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }),
  getOnboardingStatus: () => apiFetch<OnboardingStatusResponse>("/v1/onboarding/status"),
  suggestOnboardingProfile: (companyDescription: string) =>
    apiFetch<OnboardingSuggestionResponse>("/v1/onboarding/suggest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ company_description: companyDescription }),
    }),
  completeOnboarding: (body: Record<string, unknown>) =>
    apiFetch<OnboardingCompleteResponse>("/v1/onboarding/complete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  getRelevanceFeedback: () => apiFetch<RelevanceFeedbackResponse[]>("/v1/business-profile/relevance-feedback"),
  setRelevanceFeedback: (processId: string, label: RelevanceFeedbackResponse["label"], reason?: string) =>
    apiFetch<RelevanceFeedbackResponse>("/v1/business-profile/relevance-feedback", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ process_id: processId, label, reason: reason || null }),
    }),
  deleteRelevanceFeedback: (processId: string) =>
    apiFetch<void>(`/v1/business-profile/relevance-feedback/${encodeURIComponent(processId)}`, { method: "DELETE" }),
  createInvitation: (email: string, role: AccountMember["role"]) =>
    apiFetch<AccountInvitation>("/v1/account/invitations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, role }),
    }),
  revokeInvitation: (id: string) =>
    apiFetch<void>(`/v1/account/invitations/${encodeURIComponent(id)}`, { method: "DELETE" }),
  updateMemberRole: (id: string, role: AccountMember["role"]) =>
    apiFetch<AccountMember>(`/v1/account/members/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role }),
    }),
  createApiKey: (name: string, expiresInDays: number | null = 90) =>
    apiFetch<AccountApiKey>("/v1/account/api-keys", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, scopes: ["read"], expires_in_days: expiresInDays }),
    }),
  revokeApiKey: (id: string) =>
    apiFetch<void>(`/v1/account/api-keys/${encodeURIComponent(id)}`, { method: "DELETE" }),
  getPipeline: () => apiFetch<PipelineItemResponse[]>("/v1/workspace/pipeline"),
  getSavedSearches: () => apiFetch<SavedSearchResponse[]>("/v1/workspace/saved-searches"),
  createSavedSearch: (name: string, query: Record<string, unknown>) => apiFetch<SavedSearchResponse>("/v1/workspace/saved-searches", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, query }),
  }),
  deleteSavedSearch: (id: string) => apiFetch<void>(`/v1/workspace/saved-searches/${id}`, { method: "DELETE" }),
  saveToPipeline: (processId: string) => apiFetch<PipelineItemResponse>("/v1/workspace/pipeline", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ process_id: processId, stage: "WATCHING", priority: "MEDIUM" }),
  }),
  updatePipeline: (id: string, body: Record<string, unknown>) => apiFetch<PipelineItemResponse>(`/v1/workspace/pipeline/${id}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }),
  deletePipeline: (id: string) => apiFetch<void>(`/v1/workspace/pipeline/${id}`, { method: "DELETE" }),
  getWatches: (objectType?: string) => apiFetch<WatchResponse[]>(`/v1/workspace/watches${objectType ? `?object_type=${objectType}` : ""}`),
  createWatch: (objectId: string, objectType: WatchResponse["object_type"] = "COMPETITOR") => apiFetch<WatchResponse>("/v1/workspace/watches", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ object_id: objectId, object_type: objectType }),
  }),
  deleteWatch: (id: string) => apiFetch<void>(`/v1/workspace/watches/${id}`, { method: "DELETE" }),
  getNotes: (objectType: string, objectId: string) => apiFetch<NoteResponse[]>(`/v1/workspace/notes?object_type=${encodeURIComponent(objectType)}&object_id=${encodeURIComponent(objectId)}`),
  createNote: (objectType: string, objectId: string, body: string) => apiFetch<NoteResponse>("/v1/workspace/notes", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ object_type: objectType, object_id: objectId, body }),
  }),
  updateNote: (id: string, body: string) => apiFetch<NoteResponse>(`/v1/workspace/notes/${id}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ body }),
  }),
  deleteNote: (id: string) => apiFetch<void>(`/v1/workspace/notes/${id}`, { method: "DELETE" }),
  getTags: () => apiFetch<TagResponse[]>("/v1/workspace/tags"),
  getObjectTags: (objectType: string, objectId: string) => apiFetch<TagResponse[]>(`/v1/workspace/tags/links?object_type=${encodeURIComponent(objectType)}&object_id=${encodeURIComponent(objectId)}`),
  createTag: (name: string) => apiFetch<TagResponse>("/v1/workspace/tags", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }),
  }),
  linkTag: (tagId: string, objectType: string, objectId: string) => apiFetch<void>(`/v1/workspace/tags/${tagId}/links`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ object_type: objectType, object_id: objectId }),
  }),
  unlinkTag: (tagId: string, objectType: string, objectId: string) => apiFetch<void>(`/v1/workspace/tags/${tagId}/links/${encodeURIComponent(objectType)}/${encodeURIComponent(objectId)}`, { method: "DELETE" }),
  getAlertRules: () => apiFetch<AlertRuleResponse[]>("/v1/alert-rules"),
  createAlertRule: (body: Record<string, unknown>) => apiFetch<AlertRuleResponse>("/v1/alert-rules", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }),
  updateAlertRule: (id: string, body: Record<string, unknown>) => apiFetch<AlertRuleResponse>(`/v1/alert-rules/${id}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }),
  archiveAlertRule: (id: string) => apiFetch<void>(`/v1/alert-rules/${id}`, { method: "DELETE" }),
  getAlertEvents: () => apiFetch<AlertEventResponse[]>("/v1/alert-rules/events"),
  getAlertDigests: () => apiFetch<AlertDigestHistoryResponse[]>("/v1/alert-rules/digest-history"),
  getAlertDeliveries: () => apiFetch<AlertDeliveryHistoryResponse[]>("/v1/alert-rules/delivery-history"),
  markAlertRead: (id: string) => apiFetch<AlertEventResponse>(`/v1/alert-rules/events/${id}/read`, { method: "PATCH" }),
  askAssistant: (question: string, scope?: BusinessScope) => apiFetch<AssistantResponse>("/v1/intelligence/assistant", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      cpv_prefixes: scope ? activeCpvPrefixes(scope) : [],
      keywords: scope ? activeKeywords(scope) : [],
      taxonomy_match: scope && activeKeywords(scope).length ? "KEYWORD_REQUIRED" : null,
      nuts_code: scope?.nutsCode || null,
      municipality: scope?.municipality || null,
      amount_min: scope && Number(scope.amountMin) > 0 ? Number(scope.amountMin) : null,
      date_from: scope?.dateFrom || null,
      date_to: scope?.dateTo || null,
    }),
  }),
  getMarketIntelligence: (cpvPrefix?: string) => apiFetch<MarketMetricIntelligenceResponse[]>(`/v1/intelligence/markets${cpvPrefix ? `?cpv_prefix=${cpvPrefix}` : ""}`),
  getScoredOpportunities: (processId?: string, q?: string, profileVersion?: number, scope?: BusinessScope) => {
    const params = new URLSearchParams();
    if (processId) params.set("process_id", processId);
    if (q) params.set("q", q);
    if (profileVersion) params.set("profile_version", String(profileVersion));
    if (scope) {
      for (const [key, value] of Object.entries(workspaceFilterQuery(scope))) {
        params.set(key, String(value));
      }
    }
    return apiFetch<OpportunityIntelligenceResponse[]>(`/v1/intelligence/opportunities${params.size ? `?${params}` : ""}`);
  },
  getMarketDashboard: (cpvPrefix?: string) => apiFetch<MarketDashboardResponse>(`/v1/intelligence/market-dashboard${cpvPrefix ? `?cpv_prefix=${encodeURIComponent(cpvPrefix)}` : ""}`),
  getExports: () => apiFetch<ExportJobResponse[]>("/v1/exports"),
  createExport: (exportType: string, format: "CSV" | "XLSX", filters: Record<string, unknown>) => apiFetch<ExportJobResponse>("/v1/exports", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ export_type: exportType, format, filters }),
  }),
  getEntityCandidates: () => apiFetch<EntityMatchCandidateResponse[]>("/v1/entity-review/candidates"),
  generateEntityCandidates: (cursor?: string | null) => {
    const params = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
    return apiFetch<{ pairs_considered: number; candidates_written: number; identifier_conflicts: number; next_cursor: string | null; scan_complete: boolean }>(`/v1/entity-review/generate${params}`, { method: "POST" });
  },
  reviewEntityCandidate: (id: string, action: string, notes: string) => apiFetch<EntityMatchCandidateResponse>(`/v1/entity-review/candidates/${id}/review`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action, notes }),
  }),
  getEntityMerges: () => apiFetch<EntityMergeHistoryResponse[]>("/v1/entity-review/merges"),
  undoEntityMerge: (id: string) => apiFetch<EntityMergeHistoryResponse>(`/v1/entity-review/merges/${id}/undo`, { method: "POST" }),
};

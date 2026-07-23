"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useCustom, useLogout } from "@refinedev/core";
import Link from "next/link";
import dynamic from "next/dynamic";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  BarChart3,
  Bell,
  BellRing,
  Bot,
  Bookmark,
  Building2,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Database,
  Download,
  Euro,
  ExternalLink,
  FileSearch,
  Filter,
  Home,
  Landmark,
  ListTodo,
  LogOut,
  MapPinned,
  MessageSquare,
  RefreshCw,
  ReceiptText,
  Search,
  Send,
  Sparkles,
  Target,
  UsersRound,
  X,
} from "lucide-react";
import { CompetitorsWorkspace } from "@/components/competitors-workspace";
import { AlertsWorkspace } from "@/components/alerts-workspace";
import { ExportsPanel } from "@/components/exports-panel";
import { RelationshipExplorer } from "@/components/relationship-explorer";
import { EntityReviewWorkspace } from "@/components/entity-review-workspace";
import { MetricMethodologyDrawer } from "@/components/evidence-drawer";
import { DataCoveragePanel } from "@/components/data-coverage-panel";
import {
  Badge,
  EmptyState,
  ErrorState,
  IconLabel,
  LoadingState,
  MetricCard,
} from "@/components/procurement-ui";
import {
  type GeocodedLocationAnalyticsResponse,
  type BusinessProfileResponse,
  type ProfileTermResponse,
  type MarketMetricIntelligenceResponse,
  type MarketDashboardResponse,
  type OpportunityIntelligenceResponse,
  type PipelineItemResponse,
  type SavedSearchResponse,
  type MarketOverviewResponse,
  type RegionActivityResponse,
  type RegionAnalyticsResponse,
  type RenewalWatchResponse,
  type RiskIndicatorResponse,
  type SearchResponse,
  type SearchResultItem,
  type TopBuyerResponse,
  type TopSupplierResponse,
  api,
} from "@/lib/api";
import {
  activeCpvPrefixes,
  activeKeywords,
  businessScopeFingerprint,
  businessScopeQuery,
  type BusinessScope,
} from "@/lib/business-scope";

const InteractiveGreeceMap = dynamic(
  () => import("@/components/greece-nuts-map").then((module) => module.GreeceNutsMap),
  {
    ssr: false,
    loading: () => <div className="map-loading map-loading-static">Φόρτωση χάρτη…</div>,
  },
);

type HealthResponse = { status: string };
type QueryKind = "ADA" | "ADAM" | "AFM" | "TEXT" | "EMPTY";
type WorkspaceView = "home" | "opportunities" | "alerts" | "competitors" | "analytics" | "archive";
type ProfileUpdatePhase = "idle" | "saving" | "scoring" | "ready" | "error";

type BusinessProfile = BusinessScope;

type ProfileSuggestion = {
  label: string;
  cpvPrefix: string;
  keyword: string;
  evidence: string;
  confidence?: number;
};

type ChatMessage = {
  id: number;
  role: "user" | "system";
  text: string;
};

type MapRegion = {
  code: string;
  name: string;
  shortName: string;
};

const SCOPE_PREFERENCES_KEY = "procintel_workspace_scope_preferences";

const DEFAULT_PROFILE: BusinessProfile = {
  keyword: "προμήθεια",
  keywords: ["προμήθεια"],
  cpvPrefix: "",
  cpvPrefixes: [],
  nutsCode: "",
  municipality: "",
  amountMin: "0",
  dateFrom: "2026-06-01",
  dateTo: "2026-06-30",
  companyAfm: "",
};

const QUICK_PROFILES: BusinessProfile[] = [
  DEFAULT_PROFILE,
  { keyword: "αποψιλ", keywords: ["αποψιλ"], cpvPrefix: "77312000", cpvPrefixes: ["77312000", "77312100"], nutsCode: "EL3", municipality: "", amountMin: "0", dateFrom: "2026-06-01", dateTo: "2026-06-30", companyAfm: "" },
  { keyword: "λογισμικ", keywords: ["λογισμικ"], cpvPrefix: "72", cpvPrefixes: ["72", "48"], nutsCode: "", municipality: "", amountMin: "0", dateFrom: "2026-05-01", dateTo: "2026-06-30", companyAfm: "" },
];

const NUTS_REGIONS: MapRegion[] = [
  { code: "EL30", name: "Αττική", shortName: "Αττική" },
  { code: "EL41", name: "Βόρειο Αιγαίο", shortName: "Β. Αιγαίο" },
  { code: "EL42", name: "Νότιο Αιγαίο", shortName: "Ν. Αιγαίο" },
  { code: "EL43", name: "Κρήτη", shortName: "Κρήτη" },
  { code: "EL51", name: "Ανατολική Μακεδονία και Θράκη", shortName: "Αν. Μακεδονία" },
  { code: "EL52", name: "Κεντρική Μακεδονία", shortName: "Κ. Μακεδονία" },
  { code: "EL53", name: "Δυτική Μακεδονία", shortName: "Δ. Μακεδονία" },
  { code: "EL54", name: "Ήπειρος", shortName: "Ήπειρος" },
  { code: "EL61", name: "Θεσσαλία", shortName: "Θεσσαλία" },
  { code: "EL62", name: "Ιόνια Νησιά", shortName: "Ιόνια" },
  { code: "EL63", name: "Δυτική Ελλάδα", shortName: "Δ. Ελλάδα" },
  { code: "EL64", name: "Στερεά Ελλάδα", shortName: "Στερεά" },
  { code: "EL65", name: "Πελοπόννησος", shortName: "Πελοπόννησος" },
];

function toNumber(value: number | string | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeText(value: string): string {
  return value
    .toLocaleLowerCase("el-GR")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "");
}

function inferRegionCode(description: string): string {
  const normalized = normalizeText(description);
  const match = NUTS_REGIONS.find((region) => {
    return normalizeText(region.name).split(" ").some((part) => part.length > 4 && normalized.includes(part));
  });
  return match?.code ?? "";
}

function resultHref(item: SearchResultItem): string | null {
  if (item.process_id) return `/processes/${item.process_id}`;
  const identifier = item.identifier_value ?? item.adam;
  return identifier ? `/contracts/${encodeURIComponent(identifier)}` : null;
}

function detectQueryKind(value: string): QueryKind {
  const normalized = value.trim().toUpperCase();
  if (!normalized) return "EMPTY";
  if (/^\d{2}(REQ|PROC|AWRD|SYMV|PAY)[A-Z0-9]{6,}$/.test(normalized)) return "ADAM";
  if (/^[0-9A-ZΑ-Ω]{6,12}-[0-9A-ZΑ-Ω]{3,5}$/u.test(normalized)) return "ADA";
  if (/^\d{9}$/.test(normalized)) return "AFM";
  return "TEXT";
}

function queryKindLabel(kind: QueryKind): string {
  const labels: Record<QueryKind, string> = {
    ADA: "ΑΔΑ",
    ADAM: "ΑΔΑΜ",
    AFM: "ΑΦΜ",
    TEXT: "Λεκτικό τίτλου",
    EMPTY: "Αρχείο",
  };
  return labels[kind];
}

function actTypeLabel(actType: string): string {
  const labels: Record<string, string> = {
    REQUEST: "Αίτημα",
    APPROVED_REQUEST: "Έγκριση",
    NOTICE: "Προκήρυξη",
    AWARD: "Ανάθεση",
    CONTRACT: "Σύμβαση",
    AMENDMENT: "Τροποποίηση",
    CANCELLATION: "Ακύρωση",
    PAYMENT: "Πληρωμή",
    DIAVGEIA_DECISION: "Απόφαση",
    TED_NOTICE: "TED",
  };
  return labels[actType] ?? actType;
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "χωρίς ημερομηνία";
  return new Intl.DateTimeFormat("el-GR", { dateStyle: "medium" }).format(new Date(value));
}

function formatNumber(value: number | string | null | undefined): string {
  const numeric = toNumber(value);
  if (numeric === null) return "0";
  return new Intl.NumberFormat("el-GR").format(numeric);
}

function formatCurrency(value: number | string | null | undefined): string {
  const numeric = toNumber(value);
  if (numeric === null) return "0 €";
  return new Intl.NumberFormat("el-GR", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 0,
  }).format(numeric);
}

function compactCurrency(value: number | string | null | undefined): string {
  const numeric = toNumber(value);
  if (numeric === null) return "0 €";
  return new Intl.NumberFormat("el-GR", {
    style: "currency",
    currency: "EUR",
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(numeric);
}

function loadScopePreferences(): Partial<BusinessProfile> {
  if (typeof window === "undefined") return {};
  try {
    return JSON.parse(window.localStorage.getItem(SCOPE_PREFERENCES_KEY) ?? "{}") as Partial<BusinessProfile>;
  } catch {
    return {};
  }
}

function saveScopePreferences(profile: BusinessProfile) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(SCOPE_PREFERENCES_KEY, JSON.stringify({
    dateFrom: profile.dateFrom,
    dateTo: profile.dateTo,
    companyAfm: profile.companyAfm,
  }));
}

function marketQuery(profile: BusinessProfile, limit?: number): Record<string, string | number> {
  const query: Record<string, string | number> = {
    ...businessScopeQuery(profile),
    date_from: profile.dateFrom,
    date_to: profile.dateTo,
  };
  if (limit) query.limit = limit;
  return query;
}

function competitorsQuery(profile: BusinessProfile): Record<string, string | number> {
  return { ...businessScopeQuery(profile), limit: 30 };
}

function regionName(code: string): string {
  if (!code) return "Όλη η Ελλάδα";
  const normalized = code.toUpperCase();
  return (
    NUTS_REGIONS.find((region) => normalized === region.code || normalized.startsWith(region.code) || region.code.startsWith(normalized))?.name ?? code
  );
}

function resolveRegionCode(code: string): string {
  if (!code) return "";
  const normalized = code.toUpperCase();
  return (
    NUTS_REGIONS.find((region) => normalized === region.code || normalized.startsWith(region.code) || region.code.startsWith(normalized))?.code ?? normalized
  );
}

function WorkspaceScopeBar({
  profile,
  onEdit,
}: {
  profile: BusinessProfile;
  onEdit: () => void;
}) {
  const cpvPrefixes = activeCpvPrefixes(profile);
  const keywords = activeKeywords(profile);
  return (
    <section className="workspace-scope-bar" aria-label="Ενεργό επιχειρηματικό scope">
      <div className="workspace-scope-title">
        <Target size={15} aria-hidden="true" />
        <span><strong>Ενεργό προφίλ</strong><small>κοινό σε όλα τα panels</small></span>
      </div>
      <div className="workspace-scope-chips">
        {cpvPrefixes.slice(0, 4).map((prefix) => <span key={prefix}>CPV {prefix}</span>)}
        {cpvPrefixes.length > 4 && <span>+{cpvPrefixes.length - 4} CPV</span>}
        {keywords.slice(0, 3).map((keyword) => <span key={keyword}>{keyword}</span>)}
        {!cpvPrefixes.length && !keywords.length && <span>Όλες οι κατηγορίες</span>}
        <span>{regionName(profile.nutsCode)}</span>
        {profile.municipality && <span>{profile.municipality}</span>}
        <span>{profile.dateFrom} έως {profile.dateTo}</span>
      </div>
      <button type="button" onClick={onEdit}>Επεξεργασία</button>
    </section>
  );
}

function suggestionsFromTerms(terms: ProfileTermResponse[], keywordFallback = ""): ProfileSuggestion[] {
  const inferredKeyword = terms.find((term) => term.term_type === "KEYWORD" && term.is_active)?.value ?? keywordFallback;
  return terms
    .filter((term) => term.term_type === "CPV_PREFIX" && term.is_active)
    .map((term) => ({
      label: term.label,
      cpvPrefix: term.value,
      keyword: inferredKeyword || term.label,
      evidence: term.reason,
      confidence: term.confidence,
    }));
}

function suggestionsFromProfile(saved: BusinessProfileResponse): ProfileSuggestion[] {
  const suggestions = suggestionsFromTerms(saved.terms, saved.keywords[0] ?? "");
  const classifiedPrefixes = new Set(suggestions.map((suggestion) => suggestion.cpvPrefix));
  for (const cpvPrefix of saved.cpv_prefixes) {
    if (classifiedPrefixes.has(cpvPrefix)) continue;
    suggestions.push({
      label: `CPV ${cpvPrefix}`,
      cpvPrefix,
      keyword: saved.keywords[0] ?? "",
      evidence: "Αποθηκευμένη στόχευση business profile",
      confidence: 1,
    });
  }
  return suggestions;
}

function SearchResultCard({ item }: { item: SearchResultItem }) {
  const href = resultHref(item);
  const identifierScheme = item.identifier_scheme ?? (item.adam ? "ADAM" : null);
  const identifierValue = item.identifier_value ?? item.adam;
  const isExact = item.match_type === "EXACT_IDENTIFIER";
  const matchLabel = item.match_type === "EXACT_IDENTIFIER"
    ? "Ακριβής ταύτιση"
    : item.match_type === "EXACT_ENTITY_IDENTIFIER"
      ? "Ακριβές ΑΦΜ"
      : item.match_type === "EXACT_PHRASE"
        ? "Ακριβές λεκτικό"
        : "Όλοι οι όροι";

  return (
    <article className="result-card">
      <div className="result-type-icon" aria-hidden="true">
        {item.process_id ? <Landmark size={20} /> : <FileSearch size={20} />}
      </div>
      {href ? (
        <Link className="result-card-body-link" href={href}>
          <div className="result-main">
            <div className="result-badges">
              <Badge tone={isExact ? "green" : "blue"}>{matchLabel}</Badge>
              <Badge>{actTypeLabel(item.act_type)}</Badge>
              {identifierScheme && <Badge tone={identifierScheme === "ADA" ? "amber" : "neutral"}>{identifierScheme}</Badge>}
            </div>
            <h3>{item.title ?? "Χωρίς τίτλο"}</h3>
            <div className="result-meta">
              {identifierValue && <span>{identifierValue}</span>}
              {item.buyer_name && <span>{item.buyer_name}</span>}
              {item.cpv_codes.slice(0, 3).map((code) => <span key={code}>CPV {code}</span>)}
              {item.process_id && <span>Process {item.process_id}</span>}
            </div>
          </div>
        </Link>
      ) : (
        <div className="result-main">
        <div className="result-badges">
          <Badge tone={isExact ? "green" : "blue"}>{matchLabel}</Badge>
          <Badge>{actTypeLabel(item.act_type)}</Badge>
          {identifierScheme && <Badge tone={identifierScheme === "ADA" ? "amber" : "neutral"}>{identifierScheme}</Badge>}
        </div>
        <h3>{item.title ?? "Χωρίς τίτλο"}</h3>
        <div className="result-meta">
          {identifierValue && <span>{identifierValue}</span>}
          {item.buyer_name && <span>{item.buyer_name}</span>}
          {item.cpv_codes.slice(0, 3).map((code) => <span key={code}>CPV {code}</span>)}
          {item.process_id && <span>Process {item.process_id}</span>}
        </div>
      </div>
      )}
      <div className="result-source-actions">
        {item.official_url && (
          <a className="icon-button" href={item.official_url} target="_blank" rel="noreferrer" aria-label="Άνοιγμα επίσημης εγγραφής" title="Επίσημη εγγραφή">
            <ExternalLink size={16} aria-hidden="true" />
          </a>
        )}
        {item.document_url && (
          <a className="icon-button" href={item.document_url} target="_blank" rel="noreferrer" aria-label="Άνοιγμα εγγράφου προκήρυξης" title="Έγγραφο προκήρυξης">
            <Download size={16} aria-hidden="true" />
          </a>
        )}
        {href && (
          <Link className="icon-button" href={href} aria-label="Άνοιγμα στην πλατφόρμα" title="Άνοιγμα στην πλατφόρμα">
            <ArrowUpRight size={17} aria-hidden="true" />
          </Link>
        )}
      </div>
    </article>
  );
}

function OpportunityCard({ item, saved, onSave }: { item: OpportunityIntelligenceResponse; saved: boolean; onSave: () => void }) {
  const href = `/processes/${item.process_id}`;
  const score = Number(item.score ?? 0);
  const content = (
    <>
      <div className="opportunity-score" aria-label={`Fit score ${Math.round(score)}`}>
        <strong>{Math.round(score)}</strong>
        <span>fit</span>
      </div>
      <div className="opportunity-main">
        <div className="result-badges">
          <Badge tone="green">Ευκαιρία</Badge>
          {item.cpv_codes.slice(0, 2).map((code) => (
            <Badge key={code} tone="blue">
              CPV {code}
            </Badge>
          ))}
          {item.locations.slice(0, 2).map((location) => (
            <Badge key={location} tone="amber">
              {location}
            </Badge>
          ))}
        </div>
        <h3>{item.title ?? "Χωρίς τίτλο"}</h3>
        <div className="opportunity-meta">
          <span>{item.buyer_name ?? "Άγνωστος φορέας"}</span>
          <span>{formatCurrency(item.amount)}</span>
          <span>{item.deadline ? `Λήξη ${formatDate(item.deadline)}` : "Χωρίς δηλωμένη λήξη"}</span>
          {item.locations[0] && <span><MapPinned size={13} /> {item.locations.slice(0, 2).join(" · ")}</span>}
        </div>
      </div>
      <div className="opportunity-actions">
        <button className="icon-button" type="button" onClick={onSave} disabled={saved} aria-label={saved ? "Αποθηκευμένη ευκαιρία" : "Αποθήκευση στο pipeline"} title={saved ? "Στο pipeline" : "Αποθήκευση στο pipeline"}><Bookmark size={16} fill={saved ? "currentColor" : "none"} /></button>
        {item.official_url && <a className="icon-button" href={item.official_url} target="_blank" rel="noreferrer" aria-label="Άνοιγμα επίσημης εγγραφής" title="Επίσημη εγγραφή"><ExternalLink size={16} /></a>}
        {item.document_url && <a className="icon-button" href={item.document_url} target="_blank" rel="noreferrer" aria-label="Άνοιγμα εγγράφου προκήρυξης" title="Έγγραφο προκήρυξης"><Download size={16} /></a>}
        <Link className="icon-button" href={href} aria-label="Άνοιγμα ευκαιρίας"><ArrowUpRight size={18} /></Link>
      </div>
    </>
  );

  return <article className="opportunity-card">{content}</article>;
}

function Sidebar({
  active,
  onChange,
  healthStatus,
  onRefreshHealth,
}: {
  active: WorkspaceView;
  onChange: (view: WorkspaceView) => void;
  healthStatus: string;
  onRefreshHealth: () => void;
}) {
  const items: Array<{ id: WorkspaceView; label: string; icon: typeof Home }> = [
    { id: "home", label: "Προφίλ", icon: Home },
    { id: "opportunities", label: "Ευκαιρίες", icon: BellRing },
    { id: "alerts", label: "Alerts", icon: Bell },
    { id: "competitors", label: "Ανταγωνισμός", icon: UsersRound },
    { id: "analytics", label: "Analytics", icon: BarChart3 },
    { id: "archive", label: "Αρχείο", icon: FileSearch },
  ];

  return (
    <aside className="app-sidebar" aria-label="Κύρια πλοήγηση">
      <div className="sidebar-brand">
        <span className="brand-mark">
          <Landmark size={18} aria-hidden="true" />
        </span>
        <span>
          Procintel
          <small>Market intelligence</small>
        </span>
      </div>
      <nav className="sidebar-nav">
        {items.map(({ id, label, icon: Icon }) => (
          <button key={id} type="button" className={active === id ? "is-active" : ""} onClick={() => onChange(id)} aria-current={active === id ? "page" : undefined}>
            <Icon size={18} aria-hidden="true" />
            <span>{label}</span>
          </button>
        ))}
      </nav>
      <div className="sidebar-status">
        <Activity size={16} aria-hidden="true" />
        <span>{healthStatus === "online" ? "API online" : healthStatus === "checking" ? "API check" : "API offline"}</span>
        <button type="button" onClick={onRefreshHealth} aria-label="Ανανέωση API">
          <RefreshCw size={14} aria-hidden="true" />
        </button>
      </div>
    </aside>
  );
}

function ProfileHome({
  profile,
  appliedProfile,
  description,
  suggestions,
  overview,
  expandedArchive,
  opportunities,
  onDescriptionChange,
  onProfileChange,
  onApply,
  onOpenSources,
  updatePhase,
  classifying,
  feedback,
  savedAt,
}: {
  profile: BusinessProfile;
  appliedProfile: BusinessProfile;
  description: string;
  suggestions: ProfileSuggestion[];
  overview: MarketOverviewResponse | null;
  expandedArchive: boolean;
  opportunities: OpportunityIntelligenceResponse[];
  onDescriptionChange: (value: string) => void;
  onProfileChange: (profile: BusinessProfile) => void;
  onApply: () => void;
  onOpenSources: () => void;
  updatePhase: ProfileUpdatePhase;
  classifying: boolean;
  feedback: string | null;
  savedAt: string | null;
}) {
  const updating = updatePhase === "saving" || updatePhase === "scoring";
  function setField(
    key: "nutsCode" | "municipality" | "amountMin" | "dateFrom" | "dateTo" | "companyAfm",
    value: string,
  ) {
    onProfileChange({ ...profile, [key]: value });
  }

  function applySuggestion(suggestion: ProfileSuggestion) {
    const selected = profile.cpvPrefixes.includes(suggestion.cpvPrefix);
    const cpvPrefixes = selected
      ? profile.cpvPrefixes.filter((prefix) => prefix !== suggestion.cpvPrefix)
      : [...profile.cpvPrefixes, suggestion.cpvPrefix];
    const keywords = activeKeywords(profile);
    onProfileChange({
      ...profile,
      keyword: profile.keyword || suggestion.keyword,
      keywords: keywords.length ? keywords : [suggestion.keyword],
      cpvPrefixes,
      cpvPrefix: cpvPrefixes[0] ?? "",
    });
  }

  return (
    <div className="home-workspace">
      <header className="home-heading">
        <div>
          <span className="eyebrow">Company radar</span>
          <h1>Τι θέλεις να παρακολουθεί το Procintel;</h1>
          <p>Περιέγραψε την επιχείρηση με φυσική γλώσσα. Το προφίλ μετατρέπεται σε CPV, λέξεις-κλειδιά και γεωγραφική στόχευση.</p>
        </div>
        <div className="radar-state" aria-label="Κατάσταση ενεργού radar">
          <span className="live-dot" aria-hidden="true" />
          <div>
            <strong>Radar ενεργό</strong>
            <small>
              {activeKeywords(appliedProfile)[0] || "Όλες οι κατηγορίες"}
              {" · "}
              {appliedProfile.municipality || regionName(appliedProfile.nutsCode)}
            </small>
          </div>
        </div>
      </header>

      <section className="radar-builder" aria-labelledby="profile-builder-title">
        <div className="radar-editor">
          <div className="editor-heading">
            <span className="editor-icon" aria-hidden="true"><Sparkles size={18} /></span>
            <div>
              <h2 id="profile-builder-title">Προφίλ δραστηριότητας</h2>
              <p>Γράψε προϊόντα, υπηρεσίες, κλάδους πελατών και περιοχές κάλυψης.</p>
            </div>
          </div>
          <label className="profile-description">
            <span className="sr-only">Περιγραφή δραστηριότητας</span>
            <textarea
              value={description}
              onChange={(event) => onDescriptionChange(event.target.value)}
              placeholder="π.χ. Αναπτύσσουμε λογισμικό GIS, cloud υπηρεσίες και data platforms για δημόσιους φορείς."
            />
          </label>
          <div className="classification-state" role="status" aria-live="polite">
            {classifying ? <><RefreshCw size={14} className="is-spinning" /> Ανάλυση περιγραφής…</> : suggestions.length ? <><CheckCircle2 size={14} /> {suggestions.length} κατηγορίες εντοπίστηκαν</> : "Πρόσθεσε προϊόντα ή υπηρεσίες για αυτόματη κατηγοριοποίηση."}
          </div>
          <div className="tag-suggestions" role="group" aria-label="Προτεινόμενες κατηγορίες CPV">
            {suggestions.map((suggestion) => (
              <button
                key={suggestion.label}
                type="button"
                className={profile.cpvPrefixes.includes(suggestion.cpvPrefix) ? "is-selected" : ""}
                onClick={() => applySuggestion(suggestion)}
                title={suggestion.evidence}
              >
                <span className="tag-check" aria-hidden="true"><CheckCircle2 size={15} /></span>
                <span><strong>{suggestion.label}</strong><small>CPV {suggestion.cpvPrefix}{suggestion.confidence ? ` · ${Math.round(suggestion.confidence * 100)}%` : ""}</small></span>
              </button>
            ))}
          </div>
        </div>

        <aside className="radar-target" aria-label="Στόχευση radar">
          <div className="target-heading">
            <span>Στόχευση</span>
            <Badge tone="green">live preview</Badge>
          </div>
          <label className="target-field">
            <span>Keyword</span>
            <input
              aria-label="Keyword"
              value={profile.keyword}
              onChange={(event) => {
                const keywords = Array.from(new Set(
                  event.target.value.split(",").map((value) => value.trim()).filter(Boolean),
                ));
                onProfileChange({ ...profile, keyword: event.target.value, keywords });
              }}
            />
          </label>
          <label className="target-field">
            <span>Περιφέρεια</span>
            <select value={resolveRegionCode(profile.nutsCode)} onChange={(event) => setField("nutsCode", event.target.value)}>
              <option value="">Όλη η Ελλάδα</option>
              {NUTS_REGIONS.map((region) => <option key={region.code} value={region.code}>{region.name}</option>)}
            </select>
          </label>
          <div className="target-summary">
            <span><strong>{profile.cpvPrefixes.length || (profile.cpvPrefix ? 1 : "Όλα")}</strong><small>ενεργοί CPV</small></span>
            <span><strong>{formatCurrency(profile.amountMin)}</strong><small>ελάχιστη αξία</small></span>
          </div>
          {profile.cpvPrefixes.length > 0 && (
            <div className="active-cpv-list" aria-label="Ενεργοί κωδικοί CPV" tabIndex={0}>
              {profile.cpvPrefixes.map((prefix) => <span key={prefix}>CPV {prefix}</span>)}
            </div>
          )}
          {activeKeywords(profile).length > 0 && (
            <div className="active-cpv-list active-keyword-list" aria-label="Ενεργές λέξεις κλειδιά" tabIndex={0}>
              {activeKeywords(profile).map((keyword) => <span key={keyword}>{keyword}</span>)}
            </div>
          )}
          <button className="button button-primary radar-apply" type="button" onClick={onApply} disabled={updating || classifying}>
            <Target size={17} aria-hidden="true" />
            {updatePhase === "saving" ? "Αποθήκευση προφίλ…" : updatePhase === "scoring" ? "Ενημέρωση radar…" : "Αποθήκευση και ευκαιρίες"}
            <ChevronRight size={17} aria-hidden="true" />
          </button>
          {feedback && <small className={`profile-feedback is-${updatePhase}`} role={updatePhase === "error" ? "alert" : "status"}>{feedback}</small>}
          {savedAt && <small className="profile-saved-state">Αποθηκεύτηκε {formatDate(savedAt)}</small>}
        </aside>
      </section>

      <details className="advanced-profile">
        <summary><Filter size={16} aria-hidden="true" /> Προηγμένα φίλτρα αγοράς</summary>
        <div className="compact-fields">
          <label className="field-control">
            <span>CPV prefix</span>
            <input
              value={profile.cpvPrefix}
              onChange={(event) => {
                const value = event.target.value.replace(/[^\d]/g, "").slice(0, 8);
                onProfileChange({ ...profile, cpvPrefix: value, cpvPrefixes: value ? [value] : [] });
              }}
            />
          </label>
          <label className="field-control">
            <span>Ελάχιστο ποσό</span>
            <input inputMode="numeric" value={profile.amountMin} onChange={(event) => setField("amountMin", event.target.value)} />
          </label>
          <label className="field-control">
            <span>Δήμος / πόλη εκτέλεσης</span>
            <input value={profile.municipality} onChange={(event) => setField("municipality", event.target.value)} placeholder="π.χ. Ηράκλειο" />
          </label>
          <label className="field-control">
            <span>Από</span>
            <input type="date" value={profile.dateFrom} onChange={(event) => setField("dateFrom", event.target.value)} />
          </label>
          <label className="field-control">
            <span>Έως</span>
            <input type="date" value={profile.dateTo} onChange={(event) => setField("dateTo", event.target.value)} />
          </label>
          <label className="field-control">
            <span>ΑΦΜ επιχείρησης</span>
            <input inputMode="numeric" maxLength={9} value={profile.companyAfm} onChange={(event) => setField("companyAfm", event.target.value.replace(/\D/g, ""))} placeholder="για head-to-head" />
          </label>
        </div>
        <div className="profile-presets" aria-label="Έτοιμα προφίλ">
          {QUICK_PROFILES.map((preset) => (
            <button key={`${preset.keyword}-${preset.dateFrom}`} type="button" onClick={() => onProfileChange(preset)}>
              {preset.cpvPrefix === "77312000" ? "αποψιλώσεις" : preset.keyword}
            </button>
          ))}
        </div>
      </details>

      <DataCoveragePanel compact onOpen={onOpenSources} />

      {expandedArchive ? <div className="scope-fallback-notice"><Database size={15} /><span>Δεν υπάρχουν πράξεις στο επιλεγμένο προφίλ και διάστημα. Τα KPI παραμένουν στο επιλεγμένο scope και δεν αναμιγνύονται με το σύνολο της βάσης.</span></div> : null}
      <section className="market-pulse" aria-label="Σύνοψη ενεργού market">
        <div className="pulse-metric"><span><BellRing size={17} /></span><strong>{formatNumber(opportunities.length)}</strong><small>radar τελευταίων 120 ημερών</small></div>
        <div className="pulse-metric"><span><FileSearch size={17} /></span><strong>{formatNumber(overview?.opportunity_count)}</strong><small>ευκαιρίες στο επιλεγμένο διάστημα</small></div>
        <div className="pulse-metric"><span><Euro size={17} /></span><strong>{compactCurrency(overview?.recorded_contract_value)}</strong><small>συμβάσεις στο επιλεγμένο διάστημα</small></div>
        <div className="pulse-metric"><span><MapPinned size={17} /></span><strong>{formatNumber(overview?.acts_with_geo)}</strong><small>γεωσήμανση στο επιλεγμένο διάστημα</small></div>
      </section>

      <section className="home-opportunities" aria-labelledby="home-opportunities-title">
        <div className="section-heading compact-heading">
          <div><span className="eyebrow">Best matches</span><h2 id="home-opportunities-title">Πρόσφατα σήματα για την επιχείρηση</h2></div>
        </div>
        <div className="home-opportunity-list">
          {opportunities.slice(0, 3).map((item) => {
            const content = <>
              <span className="home-fit">{Math.round(Number(item.score ?? 0))}</span>
              <span className="home-opportunity-copy"><strong>{item.title ?? "Χωρίς τίτλο"}</strong><small><Building2 size={13} /> {item.buyer_name ?? "Άγνωστος φορέας"}</small></span>
              <span className="home-opportunity-meta"><strong>{formatCurrency(item.amount)}</strong><small><Clock3 size={13} /> {item.deadline ? formatDate(item.deadline) : "χωρίς λήξη"}</small></span>
              <ChevronRight size={17} aria-hidden="true" />
            </>;
            return <Link key={item.process_id} className="home-opportunity-row" href={`/processes/${item.process_id}`}>{content}</Link>;
          })}
          {!opportunities.length && <EmptyState title="Δεν υπάρχουν ακόμα ταιριαστά σήματα" detail="Εφάρμοσε διαφορετικό CPV ή μεγαλύτερο χρονικό παράθυρο." />}
        </div>
      </section>
    </div>
  );
}

function GreeceMap({
  focusCode,
  locations,
  overview,
  regions,
  expandedArchive,
  onFocus,
}: {
  focusCode: string;
  locations: GeocodedLocationAnalyticsResponse[];
  overview: MarketOverviewResponse | null;
  regions: RegionAnalyticsResponse[];
  expandedArchive: boolean;
  onFocus: (code: string) => void;
}) {
  const rankedRegions = [...regions].sort((left, right) => right.act_count - left.act_count);
  const maximum = Math.max(...rankedRegions.map((region) => region.act_count), 1);

  return (
    <section className="geo-intelligence" aria-labelledby="geo-intelligence-title">
      <div className="geo-map-column">
        <div className="geo-heading">
          <div>
            <span className="eyebrow">Geographic intelligence</span>
            <h2 id="geo-intelligence-title">Περιφέρειες και τόποι εκτέλεσης</h2>
          </div>
          {expandedArchive ? <Badge tone="amber">συνολική φορτωμένη βάση</Badge> : null}
          <div className="map-legend" aria-label="Ένταση πράξεων">
            <span>λιγότερες</span><i /><i /><i /><i /><span>περισσότερες</span>
            <b aria-hidden="true" /><span>τόπος εκτέλεσης</span>
          </div>
        </div>
        <InteractiveGreeceMap focusCode={focusCode} locations={locations} regions={regions} onFocus={onFocus} />
      </div>
      <aside className="region-intelligence" aria-label="Κατάταξη περιφερειών">
        <div className="region-ranking">
          {(rankedRegions.length ? rankedRegions : NUTS_REGIONS.map((region) => ({
            nuts_code: region.code,
            region_name: region.name,
            act_count: 0,
          }))).map((region) => (
            <button
              key={region.nuts_code}
              type="button"
              className={focusCode === region.nuts_code ? "is-active" : ""}
              onClick={() => onFocus(region.nuts_code)}
            >
              <span><strong>{region.region_name}</strong><small>{region.nuts_code}</small></span>
              <span className="region-count"><strong>{formatNumber(region.act_count)}</strong><i style={{ width: `${Math.max((region.act_count / maximum) * 100, 3)}%` }} /></span>
            </button>
          ))}
        </div>
        <p className="geo-coverage"><Database size={14} /> {formatNumber(overview?.acts_with_precise_geo)} ακριβή σημεία · {formatNumber(overview?.acts_with_geo)} με γεωγραφικό σήμα</p>
      </aside>
    </section>
  );
}

function RegionActivityPanel({
  focusCode,
  rows,
  loading,
  error,
  actType,
  onActTypeChange,
  quickFilter,
  onQuickFilterChange,
}: {
  focusCode: string;
  rows: RegionActivityResponse[];
  loading: boolean;
  error: unknown;
  actType: "CONTRACT" | "OPPORTUNITY" | "ALL";
  onActTypeChange: (value: "CONTRACT" | "OPPORTUNITY" | "ALL") => void;
  quickFilter: string;
  onQuickFilterChange: (value: string) => void;
}) {
  const selected = NUTS_REGIONS.find((region) => region.code === resolveRegionCode(focusCode)) ?? null;

  return (
    <section className="region-activity-panel" aria-labelledby="region-activity-title">
      <div className="section-heading compact-heading">
        <div>
          <span className="eyebrow">Δραστηριότητα περιοχής</span>
          <h2 id="region-activity-title">{selected?.name ?? focusCode} — τι υπάρχει εδώ</h2>
        </div>
        <div className="segmented-control" aria-label="Τύπος πράξης">
          <button type="button" className={actType === "CONTRACT" ? "is-active" : ""} onClick={() => onActTypeChange("CONTRACT")}>Συμβάσεις</button>
          <button type="button" className={actType === "OPPORTUNITY" ? "is-active" : ""} onClick={() => onActTypeChange("OPPORTUNITY")}>Προκηρύξεις</button>
          <button type="button" className={actType === "ALL" ? "is-active" : ""} onClick={() => onActTypeChange("ALL")}>Όλα</button>
        </div>
      </div>
      <div className="region-activity-filter">
        <Search size={15} aria-hidden="true" />
        <input
          type="text"
          value={quickFilter}
          onChange={(event) => onQuickFilterChange(event.target.value)}
          placeholder="Αναζήτηση ανά κωδικό CPV ή λέξη-κλειδί στον τίτλο…"
          aria-label="Αναζήτηση δραστηριότητας περιοχής ανά κωδικό CPV ή λέξη-κλειδί"
        />
      </div>
      {Boolean(error) && <ErrorState error={error} title="Δεν ήταν δυνατή η φόρτωση της δραστηριότητας περιοχής" />}
      {!error && loading && <LoadingState label="Ανάγνωση δραστηριότητας περιοχής" />}
      {!error && !loading && !rows.length && (
        <EmptyState title="Δεν βρέθηκαν εγγραφές" detail="Δοκιμάστε διαφορετικό τύπο πράξης, κωδικό CPV ή λέξη-κλειδί." />
      )}
      {!error && !loading && rows.length > 0 && (
        <div className="compact-list region-activity-list">
          {rows.map((row) => (
            <Link className="compact-row region-activity-row" key={row.act_id} href={row.process_id ? `/processes/${row.process_id}` : "#"}>
              <FileSearch size={15} aria-hidden="true" />
              <span>
                <strong>{row.title ?? "Χωρίς τίτλο"}</strong>
                <small>{row.buyer_name ?? "Άγνωστος φορέας"} · {row.act_type} · {row.cpv_codes[0] ?? "χωρίς CPV"}</small>
              </span>
              <span className="region-activity-amount">
                <strong>{compactCurrency(row.amount_gross)}</strong>
                <small>{formatDate(row.event_date)}</small>
              </span>
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}

function SupplierLeaderboard({ suppliers, loading }: { suppliers: TopSupplierResponse[]; loading: boolean }) {
  if (loading) return <LoadingState label="Ανάγνωση supplier analytics" />;
  if (!suppliers.length) return <EmptyState title="Δεν υπάρχουν καταγεγραμμένοι ανάδοχοι" detail="Τα στοιχεία θα εμφανιστούν όταν φορτωθούν συμβάσεις στο market." />;

  const maxValue = Math.max(...suppliers.map((supplier) => toNumber(supplier.recorded_value) ?? 0), 1);

  return (
    <div className="leaderboard compact-leaderboard">
      {suppliers.slice(0, 6).map((supplier, index) => {
        const value = toNumber(supplier.recorded_value) ?? 0;
        return (
          <Link className="supplier-row" key={supplier.supplier_id} href={`/companies/${supplier.supplier_id}`}>
            <span className="supplier-rank">{index + 1}</span>
            <span className="supplier-main">
              <strong>{supplier.supplier_name}</strong>
              <small>{supplier.afm ? `ΑΦΜ ${supplier.afm}` : "Χωρίς ΑΦΜ"}</small>
              <span className="supplier-bar" style={{ ["--bar-width" as string]: `${Math.max((value / maxValue) * 100, 4)}%` }} />
            </span>
            <span className="supplier-value">
              <strong>{compactCurrency(value)}</strong>
              <small>{supplier.act_count} συμβάσεις</small>
            </span>
          </Link>
        );
      })}
    </div>
  );
}

function BuyerLeaderboard({ buyers, loading }: { buyers: TopBuyerResponse[]; loading: boolean }) {
  if (loading) return <LoadingState label="Ανάγνωση buyer analytics" />;
  if (!buyers.length) return <EmptyState title="Δεν υπάρχουν καταγεγραμμένοι φορείς" detail="Τα στοιχεία θα εμφανιστούν όταν φορτωθούν συμβάσεις στο market." />;

  const maxValue = Math.max(...buyers.map((buyer) => toNumber(buyer.recorded_value) ?? 0), 1);

  return (
    <div className="leaderboard compact-leaderboard">
      {buyers.slice(0, 6).map((buyer, index) => {
        const value = toNumber(buyer.recorded_value) ?? 0;
        return (
          <Link className="supplier-row" key={buyer.buyer_id} href={`/buyers/${buyer.buyer_id}`}>
            <span className="supplier-rank">{index + 1}</span>
            <span className="supplier-main">
              <strong>{buyer.buyer_name}</strong>
              <small>{buyer.supplier_count} προμηθευτές</small>
              <span className="supplier-bar" style={{ ["--bar-width" as string]: `${Math.max((value / maxValue) * 100, 4)}%` }} />
            </span>
            <span className="supplier-value">
              <strong>{compactCurrency(value)}</strong>
              <small>{buyer.act_count} συμβάσεις</small>
            </span>
          </Link>
        );
      })}
    </div>
  );
}

function RenewalsList({ renewals, loading }: { renewals: RenewalWatchResponse[]; loading: boolean }) {
  if (loading) return <LoadingState label="Ανάγνωση επικείμενων ανανεώσεων" />;
  if (!renewals.length) return <EmptyState title="Δεν υπάρχουν επικείμενες ανανεώσεις" detail="Καμία ενεργή σύμβαση δεν πλησιάζει στη λήξη της, βάσει του μέσου χρόνου ανάθεσης του φορέα." />;

  return (
    <div className="compact-list">
      {renewals.slice(0, 8).map((renewal) => (
        <Link className="compact-row renewal-row" key={renewal.contract_act_id} href={`/processes/${renewal.process_id}`}>
          <Clock3 size={15} />
          <span>
            <strong>{renewal.title ?? "Χωρίς τίτλο"}</strong>
            <small>{renewal.buyer_name ?? "Άγνωστος φορέας"} → {renewal.supplier_name ?? "Άγνωστος ανάδοχος"}</small>
          </span>
          <strong>{renewal.days_to_end >= 0 ? `σε ${renewal.days_to_end} ημέρες` : `έληξε πριν ${Math.abs(renewal.days_to_end)} ημέρες`}</strong>
        </Link>
      ))}
    </div>
  );
}

const RISK_INDICATOR_LABELS: Record<string, string> = {
  HIGH_BUYER_CONCENTRATION: "Υψηλή συγκέντρωση αναθέσεων",
  REPEAT_SAME_CONTRACTOR: "Επαναλαμβανόμενος ανάδοχος",
  FEW_DISTINCT_SUPPLIERS: "Λίγοι διαθέσιμοι προμηθευτές",
  REPEATED_MODIFICATIONS: "Επαναλαμβανόμενες τροποποιήσεις",
  LARGE_VALUE_INCREASE: "Μεγάλη αύξηση αξίας",
  UNUSUAL_AWARD_TO_CONTRACT_DELAY: "Ασυνήθιστη καθυστέρηση ανάθεσης→σύμβασης",
  COMPANY_INACTIVE_IN_LATER_SNAPSHOT: "Ανενεργή εταιρεία σε ενεργή σύμβαση",
};

function confidenceTone(confidence: string): "green" | "amber" | "neutral" {
  if (confidence === "HIGH") return "green";
  if (confidence === "MEDIUM") return "amber";
  return "neutral";
}

function RiskIndicatorsPanel({ indicators, loading }: { indicators: RiskIndicatorResponse[]; loading: boolean }) {
  if (loading) return <LoadingState label="Υπολογισμός δεικτών ρίσκου" />;
  if (!indicators.length) return <EmptyState title="Δεν εντοπίστηκαν ασυνήθιστα μοτίβα" detail="Κανένας δείκτης δεν ξεπέρασε το ελάχιστο δείγμα ή το benchmark του." />;

  return (
    <div className="compact-list risk-indicator-list">
      {indicators.slice(0, 8).map((indicator, index) => (
        <div className="compact-row risk-indicator-row" key={`${indicator.indicator_type}-${index}`}>
          <AlertTriangle size={15} />
          <span>
            <strong>{RISK_INDICATOR_LABELS[indicator.indicator_type] ?? indicator.indicator_type}</strong>
            <small>{indicator.message}</small>
          </span>
          <Badge tone={confidenceTone(indicator.confidence)}>{indicator.confidence}</Badge>
        </div>
      ))}
    </div>
  );
}

function MarketOperations({ data }: { data: MarketDashboardResponse | null }) {
  if (!data) return <LoadingState label="Σύνθεση δεικτών αγοράς" />;
  const modificationRate = Number(data.modifications.modification_rate ?? 0) * 100;
  const paymentRatio = Number(data.payment_execution.average_execution_ratio ?? 0) * 100;

  return (
    <section className="market-operations" aria-labelledby="market-operations-title">
      <div className="section-heading compact-heading">
        <div><span className="eyebrow">Operational intelligence</span><h2 id="market-operations-title">Κύκλος, μεταβολές και ανανεώσεις</h2></div>
      </div>
      <div className="analytics-metrics analytics-metrics-secondary">
        <MetricCard label="Τροποποιημένες" value={`${modificationRate.toFixed(1)}%`} detail={`${formatNumber(data.modifications.modified_contracts)} συμβάσεις`} icon={RefreshCw} tone="amber" />
        <MetricCard label="Payment coverage" value={`${paymentRatio.toFixed(1)}%`} detail={`${formatNumber(data.payment_execution.unknown_coverage)} χωρίς κάλυψη`} icon={ReceiptText} tone="green" />
        <MetricCard label="Notice → award" value={`${formatNumber(data.cycle_time.notice_to_award_days)} ημέρες`} detail={`${formatNumber(data.cycle_time.processes_observed)} διαδικασίες`} icon={Clock3} tone="blue" />
        <MetricCard label="Renewal signals" value={formatNumber(data.signals.upcoming_renewals)} detail="εντός buyer lead time" icon={BellRing} tone="neutral" />
      </div>
      <div className="market-operation-tables">
        <div>
          <h3>Μίγμα διαδικασιών</h3>
          <div className="compact-list">
            {data.procedure_mix.slice(0, 6).map((row) => <div className="compact-row" key={String(row.procedure_type)}><Activity size={15} /><span>{String(row.procedure_type)}</span><strong>{formatNumber(row.contract_count)}</strong></div>)}
            {!data.procedure_mix.length && <EmptyState title="Δεν υπάρχουν ώριμα δεδομένα διαδικασιών" />}
          </div>
        </div>
        <div>
          <h3>Χρηματοδότηση και incumbency</h3>
          <div className="compact-list">
            <div className="compact-row"><Database size={15} /><span>Χρηματοδοτούμενα έργα</span><strong>{formatNumber(data.signals.funding_projects)}</strong></div>
            <div className="compact-row"><Euro size={15} /><span>Καταγεγραμμένο budget</span><strong>{compactCurrency(data.signals.funding_budget)}</strong></div>
            <div className="compact-row"><Target size={15} /><span>Incumbent signals</span><strong>{formatNumber(data.signals.incumbents)}</strong></div>
          </div>
        </div>
      </div>
    </section>
  );
}

function AnalyticsCopilot({
  messages,
  input,
  onInputChange,
  onSubmit,
  busy,
}: {
  messages: ChatMessage[];
  input: string;
  onInputChange: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  busy: boolean;
}) {
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [messages]);

  return (
    <section className="copilot-panel" aria-labelledby="copilot-title">
      <div className="copilot-heading">
        <div>
          <span className="eyebrow">Analytics Copilot</span>
          <h2 id="copilot-title">Ρώτα τα δεδομένα</h2>
        </div>
        <Badge tone="amber">data copilot</Badge>
      </div>
      <div className="chat-log" ref={logRef} aria-live="polite">
        {messages.map((message) => (
          <div key={message.id} className={`chat-message chat-${message.role}`}>
            <span className="chat-avatar" aria-hidden="true">
              {message.role === "system" ? <Bot size={16} /> : <MessageSquare size={16} />}
            </span>
            <p>{message.text}</p>
          </div>
        ))}
      </div>
      <form className="chat-form" onSubmit={onSubmit}>
        <label className="sr-only" htmlFor="analytics-question">
          Ερώτηση analytics
        </label>
        <input
          id="analytics-question"
          value={input}
          onChange={(event) => onInputChange(event.target.value)}
          placeholder="π.χ. Ποιοι ΑΦΜ πήραν τα περισσότερα; Δείξε Κρήτη στον χάρτη."
        />
        <button className="icon-button" type="submit" aria-label="Αποστολή ερώτησης" disabled={busy}>
          {busy ? <RefreshCw className="spin" size={17} aria-hidden="true" /> : <Send size={17} aria-hidden="true" />}
        </button>
      </form>
    </section>
  );
}

export default function IntelligencePage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const archiveInputRef = useRef<HTMLInputElement>(null);
  const initialArchiveQuery = searchParams.get("q") ?? "";
  const requestedView = searchParams.get("view") as WorkspaceView | null;
  const [profileDraft, setProfileDraft] = useState<BusinessProfile>(DEFAULT_PROFILE);
  const [appliedProfile, setAppliedProfile] = useState<BusinessProfile>(DEFAULT_PROFILE);
  const [activeView, setActiveView] = useState<WorkspaceView>(initialArchiveQuery ? "archive" : requestedView ?? "home");
  const [archiveInput, setArchiveInput] = useState(initialArchiveQuery);
  const [archiveQuery, setArchiveQuery] = useState(initialArchiveQuery);
  const [companyDescription, setCompanyDescription] = useState("Αναπτύσσουμε λογισμικό GIS, cloud υπηρεσίες και data platforms για δημόσιους φορείς.");
  const [descriptionRevision, setDescriptionRevision] = useState(0);
  const [profileSuggestions, setProfileSuggestions] = useState<ProfileSuggestion[]>([]);
  const [profileClassifying, setProfileClassifying] = useState(false);
  const [profileUpdatePhase, setProfileUpdatePhase] = useState<ProfileUpdatePhase>("idle");
  const [profileFeedback, setProfileFeedback] = useState<string | null>(null);
  const [profileSavedAt, setProfileSavedAt] = useState<string | null>(null);
  const [assistantLocations, setAssistantLocations] = useState<GeocodedLocationAnalyticsResponse[]>([]);
  const [assistantBusy, setAssistantBusy] = useState(false);
  const [opportunityMode, setOpportunityMode] = useState<"radar" | "pipeline">("radar");
  const [opportunitySearchInput, setOpportunitySearchInput] = useState("");
  const [opportunitySearch, setOpportunitySearch] = useState("");
  const [analyticsMode, setAnalyticsMode] = useState<"market" | "geography" | "relationships" | "sources" | "exports">("geography");
  const [archiveMode, setArchiveMode] = useState<"search" | "review">("search");
  const [mapFocusCode, setMapFocusCode] = useState("EL30");
  const [regionActivityActType, setRegionActivityActType] = useState<"CONTRACT" | "OPPORTUNITY" | "ALL">("CONTRACT");
  const [regionActivityQuery, setRegionActivityQuery] = useState("");
  const [chatInput, setChatInput] = useState("");
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([
    {
      id: 1,
      role: "system",
      text: "Μπορώ να εξηγήσω supplier concentration, αξία market, γεωγραφική κάλυψη και να εστιάσω τον χάρτη όταν ζητήσεις περιοχή.",
    },
  ]);

  const archiveKind = useMemo(() => detectQueryKind(archiveInput), [archiveInput]);
  const profileQuery = useMemo(() => marketQuery(appliedProfile), [appliedProfile]);
  const regionalQuery = useMemo(() => marketQuery(appliedProfile), [appliedProfile]);
  const supplierQuery = useMemo(() => marketQuery(appliedProfile, 10), [appliedProfile]);
  const competitionQuery = useMemo(() => competitorsQuery(appliedProfile), [appliedProfile]);
  const historicalCompetitionQuery = useMemo(() => {
    const query = competitorsQuery(appliedProfile);
    delete query.date_from;
    delete query.date_to;
    return query;
  }, [appliedProfile]);
  const activeScopeKey = useMemo(() => businessScopeFingerprint(appliedProfile), [appliedProfile]);
  const profileCpvQuery = activeCpvPrefixes(appliedProfile).join(",");
  const analyticsYear = appliedProfile.dateFrom.slice(0, 4) === appliedProfile.dateTo.slice(0, 4)
    ? Number(appliedProfile.dateFrom.slice(0, 4))
    : undefined;

  const health = useCustom<HealthResponse>({
    url: "/health",
    method: "get",
    queryOptions: {
      retry: 1,
      refetchInterval: 30_000,
    },
  });

  const meQuery = useCustom<{ tenant_name: string; plan: string; role: string }>({
    url: "/v1/workspace/me",
    method: "get",
    queryOptions: { retry: 1 },
  });
  const { mutate: logout } = useLogout();

  const persistedProfile = useCustom<BusinessProfileResponse>({
    url: "/v1/business-profile",
    method: "get",
    queryOptions: { retry: 1 },
  });

  const marketIntelligence = useCustom<MarketMetricIntelligenceResponse[]>({
    url: "/v1/intelligence/markets",
    method: "get",
    config: { query: {
      ...(profileCpvQuery ? { cpv_prefixes: profileCpvQuery } : {}),
      ...(analyticsYear ? { period_year: analyticsYear } : {}),
      limit: 25,
    } },
    queryOptions: { retry: 1, enabled: activeView === "analytics" },
  });
  const marketDashboard = useCustom<MarketDashboardResponse>({
    url: "/v1/intelligence/market-dashboard",
    method: "get",
    config: { query: {
      ...(profileCpvQuery ? { cpv_prefixes: profileCpvQuery } : {}),
      ...(analyticsYear ? { period_year: analyticsYear } : {}),
    } },
    queryOptions: { retry: 1, enabled: activeView === "analytics" },
  });
  const marketOverview = useCustom<MarketOverviewResponse>({
    url: "/v1/analytics/market-overview",
    method: "get",
    config: { query: profileQuery },
    queryOptions: {
      retry: 1,
    },
  });
  const overallMarketOverview = useCustom<MarketOverviewResponse>({
    url: "/v1/analytics/market-overview", method: "get", queryOptions: { retry: 1 },
  });

  const regionAnalytics = useCustom<RegionAnalyticsResponse[]>({
    url: "/v1/analytics/regions",
    method: "get",
    config: { query: regionalQuery },
    queryOptions: {
      retry: 1,
    },
  });
  const overallRegionAnalytics = useCustom<RegionAnalyticsResponse[]>({
    url: "/v1/analytics/regions", method: "get", queryOptions: { retry: 1, enabled: activeView === "analytics" },
  });

  const locationAnalytics = useCustom<GeocodedLocationAnalyticsResponse[]>({
    url: "/v1/analytics/locations",
    method: "get",
    config: { query: { ...regionalQuery, limit: 500 } },
    queryOptions: {
      retry: 1,
    },
  });
  const overallLocationAnalytics = useCustom<GeocodedLocationAnalyticsResponse[]>({
    url: "/v1/analytics/locations", method: "get", config: { query: { limit: 500 } },
    queryOptions: { retry: 1, enabled: activeView === "analytics" },
  });

  const topSuppliers = useCustom<TopSupplierResponse[]>({
    url: "/v1/analytics/top-suppliers",
    method: "get",
    config: { query: supplierQuery },
    queryOptions: {
      retry: 1,
    },
  });

  const topBuyers = useCustom<TopBuyerResponse[]>({
    url: "/v1/analytics/top-buyers",
    method: "get",
    config: { query: supplierQuery },
    queryOptions: {
      retry: 1,
      enabled: activeView === "analytics",
    },
  });

  const renewalWatch = useCustom<RenewalWatchResponse[]>({
    url: "/v1/intelligence/renewals",
    method: "get",
    config: { query: { active_only: true, limit: 10 } },
    queryOptions: {
      retry: 1,
      enabled: activeView === "analytics",
    },
  });

  const riskIndicators = useCustom<RiskIndicatorResponse[]>({
    url: "/v1/intelligence/risk-indicators",
    method: "get",
    queryOptions: {
      retry: 1,
      enabled: activeView === "analytics",
    },
  });

  const regionActivityQueryParams = useMemo(() => {
    const params: Record<string, string | number> = { ...regionalQuery };
    const trimmedQuickFilter = regionActivityQuery.trim();
    if (trimmedQuickFilter) {
      delete params.cpv_prefixes;
      delete params.keywords;
      delete params.taxonomy_match;
      if (/^\d+$/.test(trimmedQuickFilter)) {
        params.cpv_prefixes = trimmedQuickFilter;
      } else {
        params.keywords = trimmedQuickFilter;
        params.taxonomy_match = "KEYWORD_REQUIRED";
      }
    }
    params.nuts_code = mapFocusCode;
    params.limit = 30;
    if (regionActivityActType === "CONTRACT") params.act_types = "CONTRACT";
    else if (regionActivityActType === "OPPORTUNITY") params.act_types = "REQUEST,APPROVED_REQUEST,NOTICE";
    return params;
  }, [regionalQuery, regionActivityQuery, regionActivityActType, mapFocusCode]);

  const regionActivity = useCustom<RegionActivityResponse[]>({
    url: "/v1/analytics/region-activity",
    method: "get",
    config: { query: regionActivityQueryParams },
    queryOptions: {
      retry: 1,
      enabled: activeView === "analytics" && analyticsMode === "geography",
    },
  });

  const opportunityResults = useCustom<OpportunityIntelligenceResponse[]>({
    url: "/v1/intelligence/opportunities",
    method: "get",
    config: { query: opportunitySearch ? { q: opportunitySearch } : {} },
    queryOptions: {
      retry: 1,
      refetchInterval: 15_000,
    },
  });

  const pipelineResults = useCustom<PipelineItemResponse[]>({
    url: "/v1/workspace/pipeline",
    method: "get",
    queryOptions: { retry: 1 },
  });

  const archiveResults = useCustom<SearchResponse>({
    url: "/v1/search",
    method: "get",
    config: { query: { q: archiveQuery, limit: 20, auto_fetch: false } },
    queryOptions: {
      enabled: archiveQuery.trim().length > 0,
      retry: 1,
    },
  });
  const savedSearchResults = useCustom<SavedSearchResponse[]>({
    url: "/v1/workspace/saved-searches", method: "get", queryOptions: { retry: 1 },
  });

  const scopedOverview = marketOverview.query.isSuccess ? marketOverview.result.data : null;
  const allOverview = overallMarketOverview.query.isSuccess ? overallMarketOverview.result.data : null;
  const expandedArchive = Boolean(scopedOverview && scopedOverview.act_count === 0 && allOverview?.act_count);
  const overview = scopedOverview;
  const scopedRegions = Array.isArray(regionAnalytics.result.data) ? regionAnalytics.result.data : [];
  const allRegions = Array.isArray(overallRegionAnalytics.result.data) ? overallRegionAnalytics.result.data : [];
  const regions = scopedRegions.length ? scopedRegions : allRegions;
  const geocodedLocations = Array.isArray(locationAnalytics.result.data) ? locationAnalytics.result.data : [];
  const allGeocodedLocations = Array.isArray(overallLocationAnalytics.result.data) ? overallLocationAnalytics.result.data : [];
  const selectedSuppliers = Array.isArray(topSuppliers.result.data) ? topSuppliers.result.data : [];
  const suppliers = selectedSuppliers;
  const supplierScope = "Ενεργό προφίλ";
  const suppliersLoading = topSuppliers.query.isLoading;
  const suppliersError = topSuppliers.query.isError;
  const buyers = Array.isArray(topBuyers.result.data) ? topBuyers.result.data : [];
  const buyersLoading = topBuyers.query.isLoading;
  const buyersError = topBuyers.query.isError;
  const renewals = Array.isArray(renewalWatch.result.data) ? renewalWatch.result.data : [];
  const renewalsLoading = renewalWatch.query.isLoading;
  const renewalsError = renewalWatch.query.isError;
  const riskIndicatorList = Array.isArray(riskIndicators.result.data) ? riskIndicators.result.data : [];
  const riskIndicatorsLoading = riskIndicators.query.isLoading;
  const riskIndicatorsError = riskIndicators.query.isError;
  const regionActivityRows = Array.isArray(regionActivity.result.data) ? regionActivity.result.data : [];
  const regionActivityLoading = regionActivity.query.isLoading;
  const regionActivityError = regionActivity.query.isError;
  const opportunities = Array.isArray(opportunityResults.result.data) ? opportunityResults.result.data : [];
  const pipeline = Array.isArray(pipelineResults.result.data) ? pipelineResults.result.data : [];
  const pipelineByProcess = new Map(pipeline.map((item) => [item.process_id, item]));
  const archiveItems = archiveResults.query.isSuccess ? archiveResults.result.data.data : [];
  const savedSearches = Array.isArray(savedSearchResults.result.data) ? savedSearchResults.result.data : [];
  const healthStatus = health.query.isSuccess ? "online" : health.query.isLoading ? "checking" : "offline";
  const tenantName = meQuery.query.isSuccess ? meQuery.result.data.tenant_name : "Procintel workspace";
  const scopedMarketRows = Array.isArray(marketIntelligence.result.data) ? marketIntelligence.result.data : [];
  const marketRows = scopedMarketRows;
  const strongestHhi = marketRows.reduce<MarketMetricIntelligenceResponse | null>((strongest, row) => {
    if (!strongest) return row;
    return Number(row.hhi ?? 0) > Number(strongest.hhi ?? 0) ? row : strongest;
  }, null);
  const scopedDashboard = marketDashboard.query.isSuccess ? marketDashboard.result.data : null;
  const dashboard = scopedDashboard;
  const mapUsesArchive = !assistantLocations.length && (
    (!geocodedLocations.length && allGeocodedLocations.length > 0)
    || (!scopedRegions.length && allRegions.length > 0)
  );
  const mapLocations = assistantLocations.length ? assistantLocations : geocodedLocations.length ? geocodedLocations : allGeocodedLocations;

  useEffect(() => {
    if (descriptionRevision === 0) return;
    const description = companyDescription.trim();
    if (description.length < 3) return;
    let active = true;
    const timer = window.setTimeout(() => {
      void api.classifyBusinessProfile(description)
        .then((terms) => {
          if (!active) return;
          const suggestions = suggestionsFromTerms(terms);
          setProfileSuggestions(suggestions);
          const primary = suggestions[0];
          if (primary) {
            const automaticPrefixes = suggestions
              .filter((suggestion) => Number(suggestion.confidence ?? 0) >= 0.8)
              .map((suggestion) => suggestion.cpvPrefix);
            const automaticKeywords = Array.from(new Set(
              terms
                .filter((term) => term.term_type === "KEYWORD" && term.is_active)
                .map((term) => term.value.trim())
                .filter(Boolean),
            ));
            setProfileDraft((current) => ({
              ...current,
              cpvPrefix: automaticPrefixes[0] ?? primary.cpvPrefix,
              cpvPrefixes: automaticPrefixes.length ? automaticPrefixes : [primary.cpvPrefix],
              keyword: automaticKeywords.join(", ") || primary.keyword,
              keywords: automaticKeywords.length ? automaticKeywords : [primary.keyword],
            }));
          }
        })
        .catch(() => {
          if (active) setProfileSuggestions([]);
        })
        .finally(() => {
          if (active) setProfileClassifying(false);
        });
    }, 450);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [companyDescription, descriptionRevision]);

  useEffect(() => {
    if (!persistedProfile.query.isSuccess) return;
    const saved = persistedProfile.result.data;
    const preferences = loadScopePreferences();
    const next: BusinessProfile = {
      ...DEFAULT_PROFILE,
      keyword: saved.keywords.join(", "),
      keywords: saved.keywords,
      cpvPrefix: saved.cpv_prefixes[0] ?? "",
      cpvPrefixes: saved.cpv_prefixes,
      nutsCode: saved.nuts_codes[0] ?? "",
      municipality: saved.municipality ?? "",
      amountMin: saved.amount_min === null ? "0" : String(saved.amount_min),
      dateFrom: typeof preferences.dateFrom === "string" ? preferences.dateFrom : DEFAULT_PROFILE.dateFrom,
      dateTo: typeof preferences.dateTo === "string" ? preferences.dateTo : DEFAULT_PROFILE.dateTo,
      companyAfm: typeof preferences.companyAfm === "string" ? preferences.companyAfm : "",
    };
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      setCompanyDescription(saved.description);
      setProfileDraft(next);
      setAppliedProfile(next);
      setProfileSavedAt(saved.updated_at);
      setProfileSuggestions(suggestionsFromProfile(saved));
    });
    return () => { active = false; };
  }, [persistedProfile.query.isSuccess, persistedProfile.result.data]);

  function changeCompanyDescription(value: string) {
    setCompanyDescription(value);
    setDescriptionRevision((revision) => revision + 1);
    setProfileSuggestions([]);
    setProfileClassifying(value.trim().length >= 3);
    setProfileDraft((current) => ({
      ...current,
      cpvPrefix: "",
      cpvPrefixes: [],
      keyword: "",
      keywords: [],
    }));
    setProfileUpdatePhase("idle");
    setProfileFeedback(null);
  }

  async function waitForOpportunityScoring(profileUpdatedAt: string) {
    const requestedAfter = new Date(profileUpdatedAt).getTime() - 1_000;
    const deadline = Date.now() + 45_000;
    while (Date.now() < deadline) {
      const job = await api.getBusinessProfileScoringStatus();
      const isCurrentJob = job.requested_at !== null && new Date(job.requested_at).getTime() >= requestedAfter;
      if (isCurrentJob && job.status === "SUCCEEDED") return;
      if (isCurrentJob && job.status === "FAILED") {
        const detail = typeof job.error?.message === "string" ? job.error.message : "Η βαθμολόγηση ευκαιριών απέτυχε.";
        throw new Error(detail);
      }
      await new Promise((resolve) => window.setTimeout(resolve, 500));
    }
    throw new Error("Η ενημέρωση του radar δεν ολοκληρώθηκε εντός 45 δευτερολέπτων.");
  }

  async function applyProfile() {
    const inferredRegion = inferRegionCode(companyDescription);
    const nextRegion = resolveRegionCode(profileDraft.nutsCode || inferredRegion);
    setProfileUpdatePhase("saving");
    setProfileFeedback("Αποθηκεύεται το νέο εταιρικό προφίλ…");
    try {
      const saved = await api.updateBusinessProfile({
        company_name: meQuery.query.isSuccess ? meQuery.result.data.tenant_name : null,
        description: companyDescription,
        cpv_prefixes: profileDraft.cpvPrefixes.length
          ? profileDraft.cpvPrefixes
          : profileDraft.cpvPrefix ? [profileDraft.cpvPrefix] : [],
        keywords: activeKeywords(profileDraft),
        nuts_codes: nextRegion ? [nextRegion] : [],
        municipality: profileDraft.municipality || null,
        buyer_types: [], procedure_types: [],
        amount_min: Number(profileDraft.amountMin) || 0,
        amount_max: null,
        classify: true,
      });
      const nextProfile = {
        ...profileDraft,
        cpvPrefix: saved.cpv_prefixes[0] ?? profileDraft.cpvPrefix,
        cpvPrefixes: saved.cpv_prefixes,
        keyword: saved.keywords.join(", "),
        keywords: saved.keywords,
        nutsCode: saved.nuts_codes[0] ?? nextRegion,
      };
      setProfileDraft(nextProfile);
      setAppliedProfile(nextProfile);
      saveScopePreferences(nextProfile);
      setProfileSavedAt(saved.updated_at);
      setProfileSuggestions(suggestionsFromProfile(saved));
      if (nextProfile.nutsCode) setMapFocusCode(resolveRegionCode(nextProfile.nutsCode));
      setProfileUpdatePhase("scoring");
      setProfileFeedback("Το προφίλ αποθηκεύτηκε. Επαναϋπολογίζονται οι ταιριαστές ευκαιρίες…");
      await waitForOpportunityScoring(saved.updated_at);
      const [refreshedOpportunities] = await Promise.all([
        opportunityResults.query.refetch(),
        pipelineResults.query.refetch(),
      ]);
      const matchCount = refreshedOpportunities.data?.data.length ?? 0;
      setProfileUpdatePhase("ready");
      setProfileFeedback(`Το radar ενημερώθηκε με ${matchCount} ταιριαστές ευκαιρίες για το νέο προφίλ.`);
      setActiveView("opportunities");
    } catch (error) {
      setProfileUpdatePhase("error");
      setProfileFeedback(error instanceof Error ? error.message : "Δεν ολοκληρώθηκε η ενημέρωση του προφίλ.");
    }
  }

  function runArchiveSearch(value: string) {
    const nextQuery = value.trim();
    setArchiveInput(nextQuery);
    setArchiveQuery(nextQuery);
    setActiveView("archive");
    router.replace(nextQuery ? `/?q=${encodeURIComponent(nextQuery)}` : "/", { scroll: false });
  }

  function onArchiveSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    runArchiveSearch(archiveInput);
  }

  function onOpportunitySearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setOpportunitySearch(opportunitySearchInput.trim());
  }

  function clearOpportunitySearch() {
    setOpportunitySearchInput("");
    setOpportunitySearch("");
  }

  async function saveArchiveSearch() {
    const value = archiveInput.trim();
    if (!value) return;
    await api.createSavedSearch(value, { q: value, kind: detectQueryKind(value) });
    await savedSearchResults.query.refetch();
  }

  async function onChatSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const question = chatInput.trim();
    if (!question) return;
    setChatMessages((current) => [...current, { id: Date.now(), role: "user", text: question }]);
    setChatInput("");
    setAssistantBusy(true);
    try {
      const answer = await api.askAssistant(question, appliedProfile);
      const region = inferRegionCode(question);
      const regionAcknowledgement = region ? `Εστίασα τον χάρτη στην περιοχή ${regionName(region)}. ` : "";
      setChatMessages((current) => [...current, { id: Date.now() + 1, role: "system", text: `${regionAcknowledgement}${answer.answer} ${answer.methodology}` }]);
      if (answer.visualization.type === "MAP") {
        setAssistantLocations(answer.data.flatMap((row) => {
          const latitude = Number(row.latitude);
          const longitude = Number(row.longitude);
          if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return [];
          return [{ label: String(row.label ?? "Τόπος εκτέλεσης"), nuts_code: null, municipality_name: null, regional_unit_name: null, region_name: null, latitude, longitude, act_count: Number(row.act_count ?? 0), opportunity_count: Number(row.act_count ?? 0), contract_count: 0, recorded_contract_value: Number(row.value ?? 0), minimum_confidence: null }];
        }));
      } else {
        setAssistantLocations([]);
      }
      if (region) setMapFocusCode(region);
    } catch {
      setChatMessages((current) => [...current, { id: Date.now() + 1, role: "system", text: "Δεν μπόρεσα να εκτελέσω την ανάλυση. Τα υπάρχοντα market panels παραμένουν διαθέσιμα." }]);
    } finally {
      setAssistantBusy(false);
    }
  }

  async function saveOpportunity(item: OpportunityIntelligenceResponse) {
    if (pipelineByProcess.has(item.process_id)) return;
    await api.saveToPipeline(item.process_id);
    await pipelineResults.query.refetch();
  }

  async function changePipelineStage(item: PipelineItemResponse, stage: string) {
    await api.updatePipeline(item.id, { stage });
    await pipelineResults.query.refetch();
  }

  function openArchiveLookup() {
    setActiveView("archive");
    window.setTimeout(() => archiveInputRef.current?.focus(), 0);
  }

  useEffect(() => {
    function onGlobalShortcut(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLocaleLowerCase() === "k") {
        event.preventDefault();
        openArchiveLookup();
      }
    }
    window.addEventListener("keydown", onGlobalShortcut);
    return () => window.removeEventListener("keydown", onGlobalShortcut);
  }, []);

  return (
    <div className="procintel-shell">
      <a href="#workspace-content" className="skip-link">
        Μετάβαση στο περιεχόμενο
      </a>
      <Sidebar active={activeView} onChange={setActiveView} healthStatus={healthStatus} onRefreshHealth={() => health.query.refetch()} />

      <main id="workspace-content" className="workspace-content">
        <header className="workspace-topbar">
          <div className="workspace-account">
            <span className="account-mark" aria-hidden="true">DI</span>
            <div><strong>{tenantName}</strong><small>{meQuery.query.isSuccess ? `${meQuery.result.data.plan} · ${meQuery.result.data.role}` : "Public sector workspace"}</small></div>
          </div>
          <button className="global-lookup" type="button" onClick={openArchiveLookup}>
            <Search size={16} aria-hidden="true" />
            <span>Αναζήτηση ΑΔΑ, ΑΔΑΜ, ΑΦΜ…</span>
            <kbd>⌘ K</kbd>
          </button>
          <div className="topbar-status"><span className="live-dot" /><span>Data έως 30 Ιουν 2026</span></div>
          {meQuery.query.isError ? (
            <a className="button button-secondary" href="/login">Σύνδεση</a>
          ) : (
            <button className="button button-secondary" type="button" onClick={() => logout()}>
              <LogOut size={15} aria-hidden="true" />
              <span>Αποσύνδεση</span>
            </button>
          )}
        </header>

        {!["home", "archive"].includes(activeView) && (
          <WorkspaceScopeBar profile={appliedProfile} onEdit={() => setActiveView("home")} />
        )}

        {activeView === "home" && (
          <ProfileHome
            profile={profileDraft}
            appliedProfile={appliedProfile}
            description={companyDescription}
            suggestions={profileSuggestions}
            overview={overview}
            expandedArchive={expandedArchive}
            opportunities={opportunities}
            onDescriptionChange={changeCompanyDescription}
            onProfileChange={setProfileDraft}
            onApply={applyProfile}
            onOpenSources={() => { setActiveView("analytics"); setAnalyticsMode("sources"); }}
            updatePhase={profileUpdatePhase}
            classifying={profileClassifying}
            feedback={profileFeedback}
            savedAt={profileSavedAt}
          />
        )}

        {activeView === "opportunities" && (
          <div className="view-stack">
            <div className="view-heading">
              <div>
                <span className="eyebrow">Ευκαιρίες</span>
                <h1>Ευκαιρίες radar</h1>
              </div>
              <div className="view-heading-actions"><div className="segmented-control" aria-label="Προβολή ευκαιριών"><button type="button" className={opportunityMode === "radar" ? "is-active" : ""} onClick={() => setOpportunityMode("radar")}><Target size={15} />Radar</button><button type="button" className={opportunityMode === "pipeline" ? "is-active" : ""} onClick={() => setOpportunityMode("pipeline")}><ListTodo size={15} />Pipeline</button></div><button className="icon-button" type="button" onClick={() => opportunityResults.query.refetch()} aria-label="Ανανέωση ευκαιριών"><RefreshCw size={17} aria-hidden="true" /></button></div>
            </div>
            {profileFeedback && profileUpdatePhase !== "idle" && <div className={`profile-update-notice is-${profileUpdatePhase}`} role={profileUpdatePhase === "error" ? "alert" : "status"}>{profileUpdatePhase === "ready" ? <CheckCircle2 size={16} /> : <RefreshCw size={16} className={profileUpdatePhase === "scoring" ? "is-spinning" : ""} />}<span>{profileFeedback}</span>{profileUpdatePhase === "ready" && <button type="button" onClick={() => setActiveView("home")}>Προβολή προφίλ</button>}</div>}
            {opportunityMode === "radar" && (
              <form className="command-search opportunity-search" onSubmit={onOpportunitySearch}>
                <div className="search-input-wrap">
                  <Search size={19} aria-hidden="true" />
                  <input
                    value={opportunitySearchInput}
                    onChange={(event) => setOpportunitySearchInput(event.target.value)}
                    placeholder="Λεκτικό στον τίτλο, π.χ. GIS"
                    aria-label="Λεκτική αναζήτηση ευκαιριών"
                  />
                  {opportunitySearchInput && (
                    <button className="icon-button search-clear" type="button" onClick={clearOpportunitySearch} aria-label="Καθαρισμός λεκτικής αναζήτησης">
                      <X size={16} aria-hidden="true" />
                    </button>
                  )}
                </div>
                <button className="button button-primary" type="submit">
                  <Search size={17} aria-hidden="true" />
                  Αναζήτηση
                </button>
              </form>
            )}
            {opportunityMode === "radar" && opportunityResults.query.isError && <ErrorState error={opportunityResults.query.error} title="Δεν είναι διαθέσιμες οι ευκαιρίες" />}
            {opportunityMode === "radar" && opportunityResults.query.isLoading && <LoadingState label="Ανάγνωση ευκαιριών" />}
            {opportunityMode === "radar" && !opportunityResults.query.isLoading && !opportunities.length && (
              <EmptyState title="Δεν υπάρχουν ακόμα ευκαιρίες για το προφίλ" detail={<IconLabel icon={Database}>Άλλαξε CPV/περιοχή ή επέκτεινε το διαθέσιμο dataset μήνα.</IconLabel>} />
            )}
            {opportunityMode === "radar" && opportunities.length > 0 && (
              <div className="opportunity-list compact-list-panel">
                {opportunities.slice(0, 8).map((item) => (
                  <OpportunityCard key={item.process_id} item={item} saved={pipelineByProcess.has(item.process_id)} onSave={() => void saveOpportunity(item)} />
                ))}
              </div>
            )}
            {opportunityMode === "pipeline" && pipelineResults.query.isLoading && <LoadingState label="Φόρτωση εμπορικού pipeline" />}
            {opportunityMode === "pipeline" && !pipelineResults.query.isLoading && !pipeline.length && <EmptyState title="Το pipeline είναι κενό" detail="Αποθήκευσε μια ευκαιρία από το Radar για να ξεκινήσεις qualification." />}
            {opportunityMode === "pipeline" && pipeline.length > 0 && <div className="pipeline-table" role="table" aria-label="Opportunity pipeline"><div className="pipeline-table-head" role="row"><span>Ευκαιρία</span><span>Stage</span><span>Priority</span><span>Score</span></div>{pipeline.map((item) => <div className="pipeline-table-row" role="row" key={item.id}><Link href={`/processes/${item.process_id}`}><strong>{item.process_title ?? item.process_id}</strong><small>{item.next_action ?? "Χωρίς επόμενο βήμα"}</small></Link><select value={item.stage} onChange={(event) => void changePipelineStage(item, event.target.value)} aria-label={`Stage για ${item.process_title}`}><option value="WATCHING">Watching</option><option value="QUALIFYING">Qualifying</option><option value="BID_NO_BID">Bid / No bid</option><option value="BIDDING">Bidding</option><option value="WON">Won</option><option value="LOST">Lost</option><option value="DROPPED">Dropped</option></select><Badge tone={item.priority === "HIGH" || item.priority === "URGENT" ? "amber" : "neutral"}>{item.priority}</Badge><strong>{item.opportunity_score ? Math.round(Number(item.opportunity_score)) : "-"}</strong></div>)}</div>}
          </div>
        )}

        {activeView === "alerts" && <AlertsWorkspace key={activeScopeKey} profile={appliedProfile} />}

        {activeView === "competitors" && (
          <CompetitorsWorkspace
            key={activeScopeKey}
            query={competitionQuery}
            historicalQuery={historicalCompetitionQuery}
            globalQuery={{ limit: 30 }}
            referenceAfm={appliedProfile.companyAfm}
            scopeLabel={`${activeCpvPrefixes(appliedProfile).length || "Όλα"} CPV · ${activeKeywords(appliedProfile).join(", ") || "όλα τα αντικείμενα"} · ${regionName(appliedProfile.nutsCode)} · ${appliedProfile.dateFrom} - ${appliedProfile.dateTo}`}
          />
        )}

        {activeView === "analytics" && (
          <div className="view-stack analytics-view">
            <div className="view-heading">
              <div>
                <span className="eyebrow">Analytics</span>
                <h1>Ανάλυση αγοράς</h1>
              </div>
              <div className="segmented-control analytics-segments" aria-label="Προβολή analytics">
                <button type="button" className={analyticsMode === "market" ? "is-active" : ""} onClick={() => setAnalyticsMode("market")}><BarChart3 size={14} />Αγορά</button>
                <button type="button" className={analyticsMode === "geography" ? "is-active" : ""} onClick={() => setAnalyticsMode("geography")}><MapPinned size={14} />Χάρτης</button>
                <button type="button" className={analyticsMode === "relationships" ? "is-active" : ""} onClick={() => setAnalyticsMode("relationships")}><UsersRound size={14} />Σχέσεις</button>
                <button type="button" className={analyticsMode === "sources" ? "is-active" : ""} onClick={() => setAnalyticsMode("sources")}><Activity size={14} />Πηγές</button>
                <button type="button" className={analyticsMode === "exports" ? "is-active" : ""} onClick={() => setAnalyticsMode("exports")}><Database size={14} />Exports</button>
              </div>
            </div>
            {analyticsMode === "market" && <>
              <section className="analytics-metrics" aria-label="Market metrics">
                <MetricCard label="Διαδικασίες" value={formatNumber(overview?.process_count)} detail={activeKeywords(appliedProfile).join(", ") || "όλα"} icon={Landmark} tone="blue" />
                <MetricCard label="Ευκαιρίες" value={formatNumber(overview?.opportunity_count)} detail={`${formatNumber(overview?.notice_count)} δημοσιευμένες προκηρύξεις`} icon={BellRing} tone="green" />
                <MetricCard label="Συμβάσεις" value={formatNumber(overview?.contract_count)} detail={formatCurrency(overview?.recorded_contract_value)} icon={Euro} tone="amber" />
                <MetricCard label="Ακριβής γεωγραφία" value={formatNumber(overview?.acts_with_precise_geo)} detail={`${formatNumber(overview?.acts_with_geo)} με NUTS ή τόπο`} icon={MapPinned} tone="neutral" />
              </section>
              <section className="market-method-strip" aria-label="Market concentration methodology">
                <div><span>HHI συγκέντρωσης</span><strong>{strongestHhi?.hhi ? formatNumber(strongestHhi.hhi) : "χωρίς επαρκές δείγμα"}</strong></div>
                <div><span>Προμηθευτές</span><strong>{formatNumber(strongestHhi?.supplier_count)}</strong></div>
                <div><span>Διάμεση σύμβαση</span><strong>{formatCurrency(strongestHhi?.median_value)}</strong></div>
                <p>Υπολογισμός με καταγεγραμμένη καθαρή αξία. Ο δείκτης συγκέντρωσης δεν αποτελεί ένδειξη εύνοιας. <MetricMethodologyDrawer metric="hhi" /></p>
              </section>
              <div className="analytics-market-grid">
                <section className="analytics-card supplier-intelligence" aria-labelledby="supplier-title">
                  <div className="section-heading compact-heading"><div><span className="eyebrow">Supplier intelligence</span><h2 id="supplier-title">ΑΦΜ με τη μεγαλύτερη καταγεγραμμένη αξία</h2></div><Badge tone={selectedSuppliers.length ? "blue" : "amber"}>{supplierScope}</Badge></div>
                  {suppliersError && <ErrorState error={topSuppliers.query.error} title="Δεν είναι διαθέσιμα τα supplier analytics" />}
                  {!suppliersError && <SupplierLeaderboard suppliers={suppliers} loading={suppliersLoading} />}
                </section>
                <MarketOperations data={dashboard} />
              </div>
              <div className="analytics-market-grid-secondary">
                <section className="analytics-card buyer-intelligence" aria-labelledby="buyer-title">
                  <div className="section-heading compact-heading"><div><span className="eyebrow">Buyer intelligence</span><h2 id="buyer-title">Φορείς με τη μεγαλύτερη καταγεγραμμένη αξία</h2></div></div>
                  {buyersError && <ErrorState error={topBuyers.query.error} title="Δεν είναι διαθέσιμα τα buyer analytics" />}
                  {!buyersError && <BuyerLeaderboard buyers={buyers} loading={buyersLoading} />}
                </section>
                <section className="analytics-card renewals-pipeline" aria-labelledby="renewals-title">
                  <div className="section-heading compact-heading"><div><span className="eyebrow">Renewal pipeline</span><h2 id="renewals-title">Επικείμενες ανανεώσεις</h2></div></div>
                  {renewalsError && <ErrorState error={renewalWatch.query.error} title="Δεν είναι διαθέσιμο το renewal pipeline" />}
                  {!renewalsError && <RenewalsList renewals={renewals} loading={renewalsLoading} />}
                </section>
                <section className="analytics-card risk-indicators" aria-labelledby="risk-title">
                  <div className="section-heading compact-heading"><div><span className="eyebrow">Risk &amp; anomaly indicators</span><h2 id="risk-title">Ασυνήθιστα μοτίβα προς εξέταση</h2></div></div>
                  {riskIndicatorsError && <ErrorState error={riskIndicators.query.error} title="Δεν είναι διαθέσιμοι οι δείκτες ρίσκου" />}
                  {!riskIndicatorsError && <RiskIndicatorsPanel indicators={riskIndicatorList} loading={riskIndicatorsLoading} />}
                </section>
              </div>
            </>}
            {analyticsMode === "geography" && <>
              <div className="analytics-command-grid"><GreeceMap focusCode={mapFocusCode} locations={mapLocations} overview={mapUsesArchive ? allOverview : overview} regions={regions} expandedArchive={mapUsesArchive} onFocus={setMapFocusCode} /><AnalyticsCopilot messages={chatMessages} input={chatInput} onInputChange={setChatInput} onSubmit={onChatSubmit} busy={assistantBusy} /></div>
              <RegionActivityPanel
                focusCode={mapFocusCode}
                rows={regionActivityRows}
                loading={regionActivityLoading}
                error={regionActivityError ? regionActivity.query.error : null}
                actType={regionActivityActType}
                onActTypeChange={setRegionActivityActType}
                quickFilter={regionActivityQuery}
                onQuickFilterChange={setRegionActivityQuery}
              />
            </>}
            {analyticsMode === "relationships" && <RelationshipExplorer key={activeScopeKey} profile={appliedProfile} />}
            {analyticsMode === "sources" && <DataCoveragePanel />}
            {analyticsMode === "exports" && <ExportsPanel key={activeScopeKey} profile={appliedProfile} />}
          </div>
        )}

        {activeView === "archive" && (
          <div className="view-stack archive-view">
            <div className="view-heading">
              <div>
                <span className="eyebrow">Αρχείο</span>
                <h1>{archiveMode === "search" ? "Αναζήτηση στο φορτωμένο αρχείο" : "Έλεγχος canonical οντοτήτων"}</h1>
              </div>
              <div className="view-heading-actions"><div className="segmented-control"><button type="button" className={archiveMode === "search" ? "is-active" : ""} onClick={() => setArchiveMode("search")}><Search size={14} />Αναζήτηση</button>{meQuery.result.data?.role && ["OWNER", "ADMIN"].includes(meQuery.result.data.role) && <button type="button" className={archiveMode === "review" ? "is-active" : ""} onClick={() => setArchiveMode("review")}><CheckCircle2 size={14} />Entity review</button>}</div>{archiveMode === "search" && <button className="button button-secondary" type="button" onClick={() => void saveArchiveSearch()} disabled={!archiveInput.trim()}><Bookmark size={15} />Αποθήκευση</button>}</div>
            </div>
            {archiveMode === "search" && <>
            <form className="command-search archive-search" onSubmit={onArchiveSubmit}>
              <div className="search-input-wrap">
                <Search size={21} aria-hidden="true" />
                <input ref={archiveInputRef} value={archiveInput} onChange={(event) => setArchiveInput(event.target.value)} placeholder="Λεκτικό τίτλου, ΑΔΑΜ, ΑΔΑ ή ΑΦΜ" aria-label="Αναζήτηση στο αρχείο" />
                <span className="query-kind">{queryKindLabel(archiveKind)}</span>
              </div>
              <button className="button button-primary" type="submit">
                <FileSearch size={18} aria-hidden="true" />
                Έλεγχος
              </button>
            </form>
            {savedSearches.length > 0 && <div className="saved-search-strip" aria-label="Αποθηκευμένες αναζητήσεις">{savedSearches.slice(0, 8).map((saved) => <span key={saved.id}><button type="button" onClick={() => runArchiveSearch(String(saved.query.q ?? saved.name))}>{saved.name}</button><button type="button" aria-label={`Διαγραφή ${saved.name}`} onClick={() => void api.deleteSavedSearch(saved.id).then(() => savedSearchResults.query.refetch())}>×</button></span>)}</div>}
            <div className="archive-grid">
              <section>
                {health.query.isError && <ErrorState title="Το API δεν είναι διαθέσιμο" error={health.query.error} />}
                {archiveResults.query.isError && <ErrorState error={archiveResults.query.error} title="Δεν είναι διαθέσιμη η αναζήτηση αρχείου" />}
                {archiveResults.query.isLoading && <LoadingState label="Ανάγνωση τοπικού αρχείου" />}
                {!archiveQuery && <EmptyState title="Δεν έχει γίνει αναζήτηση" detail={<IconLabel icon={Filter}>ΑΔΑΜ, ΑΔΑ, ΑΦΜ ή τίτλος</IconLabel>} />}
                {archiveQuery && !archiveResults.query.isLoading && !archiveResults.query.isError && archiveItems.length === 0 && (
                  <EmptyState title="Δεν υπάρχει στα φορτωμένα δεδομένα" detail="Θα εμφανιστεί μετά τον επόμενο κύκλο ενημέρωσης ή μετά από στοχευμένη εισαγωγή." />
                )}
                {archiveItems.length > 0 && (
                  <div className="result-list archive-results">
                    {archiveItems.slice(0, 8).map((item) => (
                      <SearchResultCard key={item.act_id} item={item} />
                    ))}
                  </div>
                )}
              </section>
              <DataCoveragePanel compact onOpen={() => { setActiveView("analytics"); setAnalyticsMode("sources"); }} />
            </div>
            </>}
            {archiveMode === "review" && <EntityReviewWorkspace />}
          </div>
        )}
      </main>
    </div>
  );
}

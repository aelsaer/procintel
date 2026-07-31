"use client";

import { use, useState } from "react";
import { useCustom, useOne } from "@refinedev/core";
import Link from "next/link";
import { CalendarClock, Download, ExternalLink, FileCheck2, Files, Landmark, MapPinned, Radar, ReceiptText, ShieldCheck, Trophy, Users, UsersRound } from "lucide-react";
import {
  BackLink,
  Badge,
  EmptyState,
  ErrorState,
  KeyValue,
  KeyValueList,
  LoadingState,
  MetricCard,
  PageHeader,
  Section,
} from "@/components/procurement-ui";
import type { OpportunityIntelligenceResponse, ProcessCompetitionResponse, ProcessDetailResponse, ProcessParticipant, ProcessTimelineResponse, SimilarContractResponse } from "@/lib/api";
import { formatAmount, formatDate } from "@/lib/format";
import { EvidenceDrawer } from "@/components/evidence-drawer";
import { ObjectWorkspace } from "@/components/object-workspace";
import { BidWorkspacePanel } from "@/components/bid-workspace";
import { ProcessDocumentTools } from "@/components/process-document-tools";
import { DocumentIntelligence } from "@/components/document-intelligence";
import { PublicationSources, TenderSummarySection } from "@/components/tender-publication";
import { BidReportPanel } from "@/components/bid-report-panel";

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}

function valueText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function pickText(record: Record<string, unknown>, keys: string[]): string | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return null;
}

function GenericRecordList({
  records,
  titleKeys,
  metaKeys,
  emptyTitle,
}: {
  records: Record<string, unknown>[];
  titleKeys: string[];
  metaKeys: string[];
  emptyTitle: string;
}) {
  if (records.length === 0) return <EmptyState title={emptyTitle} />;

  return (
    <div className="compact-list">
      {records.slice(0, 8).map((record, index) => {
        const title = pickText(record, titleKeys) ?? `#${index + 1}`;
        const meta = metaKeys
          .map((key) => valueText(record[key]))
          .filter((value) => value !== "—")
          .join(" · ");

        return (
          <div className="compact-row" key={`${title}-${index}`}>
            <Files size={16} aria-hidden="true" />
            <span>{title}</span>
            {meta && <strong>{meta}</strong>}
          </div>
        );
      })}
    </div>
  );
}

function countText(count: number): string {
  return count === 1 ? "1 εγγραφή" : `${count} εγγραφές`;
}

function LocationList({ locations }: { locations: Record<string, unknown>[] }) {
  if (locations.length === 0) return <EmptyState title="Δεν υπάρχει γεωγραφικό context" />;

  return (
    <div className="compact-list">
      {locations.map((location, index) => {
        const nuts = valueText(location.nuts_code);
        const municipality = valueText(location.municipality_name ?? location.place_text ?? location.municipality_code);
        const regionalUnit = valueText(location.regional_unit_name);
        const region = valueText(location.region_name);
        const postal = valueText(location.postal_code);
        const latitude = typeof location.latitude === "number" ? location.latitude : null;
        const longitude = typeof location.longitude === "number" ? location.longitude : null;
        const confidence = typeof location.confidence === "number" ? `${Math.round(location.confidence * 100)}%` : null;
        const meta = [regionalUnit, region, postal !== "—" ? `ΤΚ ${postal}` : "—", nuts !== "—" ? `NUTS ${nuts}` : "—"]
          .filter((value) => value !== "—")
          .join(" · ");

        return (
          <div className="compact-row" key={`${nuts}-${municipality}-${postal}-${index}`}>
            <MapPinned size={16} aria-hidden="true" />
            <span>
              {latitude !== null && longitude !== null ? (
                <a
                  className="text-link"
                  href={`https://www.openstreetmap.org/?mlat=${latitude}&mlon=${longitude}#map=14/${latitude}/${longitude}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  {municipality}
                </a>
              ) : municipality}
              {meta && <small>{meta}</small>}
            </span>
            <strong>{confidence ? `${confidence} confidence` : "Πηγή χωρίς geocode"}</strong>
          </div>
        );
      })}
    </div>
  );
}

function EnrichmentCoverage({ process }: { process: ProcessDetailResponse }) {
  const rows = [
    ["Διαύγεια", process.diavgeia_decisions.length],
    ["ΓΕΜΗ", process.supplier_company_info.length],
    ["Χρηματοδότηση", process.funding_projects.length],
    ["TED", process.ted_notices.length],
    ["ΜΕΦ", process.mef_expense_signals.length],
    ["Γεωγραφία", process.locations.length],
    ["Έγγραφα", process.documents.length],
  ] as const;

  return (
    <div className="compact-list">
      {rows.map(([label, count]) => (
        <div className="compact-row" key={label}>
          <ShieldCheck size={16} aria-hidden="true" />
          <span>{label}</span>
          <strong>{countText(count)}</strong>
        </div>
      ))}
    </div>
  );
}

function CompetitionParticipantRow({ participant }: { participant: ProcessParticipant }) {
  const confirmed = participant.classification !== "INFERRED_MARKET_COMPETITOR";
  const content = (
    <>
      <span className={`competition-person-icon ${confirmed ? "is-confirmed" : "is-inferred"}`} aria-hidden="true">
        {participant.role === "WINNER" ? <Trophy size={15} /> : confirmed ? <ShieldCheck size={15} /> : <Radar size={15} />}
      </span>
      <span className="competition-person-copy">
        <strong>{participant.name}</strong>
        <small>{participant.afm ? `ΑΦΜ ${participant.afm} · ` : ""}{participant.evidence_label}</small>
      </span>
      <span className="competition-person-proof">
        <Badge tone={confirmed ? (participant.role === "WINNER" ? "green" : "blue") : "amber"}>
          {participant.role === "WINNER" ? "Ανάδοχος" : confirmed ? "Συμμετέχων" : "Πιθανός"}
        </Badge>
        <small>{Math.round(participant.confidence * 100)}% confidence</small>
      </span>
    </>
  );

  return participant.company_id ? (
    <Link className="competition-person" href={`/companies/${participant.company_id}`}>{content}</Link>
  ) : (
    <div className="competition-person">{content}</div>
  );
}

export default function ProcessPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [activeTab, setActiveTab] = useState<"overview" | "documents" | "bid" | "buyer" | "competitors" | "similar" | "lifecycle" | "funding" | "notes">("overview");
  const [reportOpen, setReportOpen] = useState(false);

  const processQuery = useOne<ProcessDetailResponse>({
    resource: "processes",
    id,
    queryOptions: {
      retry: 1,
    },
  });
  const timelineQuery = useOne<ProcessTimelineResponse>({
    resource: "process-timelines",
    id,
    queryOptions: {
      enabled: activeTab === "lifecycle",
      retry: 1,
    },
  });
  const competitionQuery = useCustom<ProcessCompetitionResponse>({
    url: `/v1/processes/${id}/competition`,
    method: "get",
    queryOptions: { enabled: activeTab === "competitors", retry: 1 },
  });
  const similarQuery = useCustom<SimilarContractResponse[]>({
    url: `/v1/processes/${id}/similar-contracts`, method: "get",
    queryOptions: { enabled: activeTab === "similar", retry: 1 },
  });
  const scoreQuery = useCustom<OpportunityIntelligenceResponse[]>({
    url: "/v1/intelligence/opportunities", method: "get",
    config: { query: { process_id: id, limit: 1 } }, queryOptions: { retry: 1 },
  });

  if (processQuery.query.isLoading) return <LoadingState label="Φόρτωση διαδικασίας" />;
  if (processQuery.query.isError) return <ErrorState error={processQuery.query.error} />;

  const process = processQuery.result;
  if (!process) return <EmptyState title="Δεν βρέθηκε διαδικασία" />;

  const dataQuality = asRecord(process.data_quality);
  const score = scoreQuery.query.isSuccess ? scoreQuery.result.data[0] : null;
  const primaryPublication = process.official_records.find((record) => record.official_url || record.document_url);
  const tabs = [
    ["overview", "Overview"], ["documents", "Documents"], ["bid", "Bid workspace"], ["buyer", "Buyer history"],
    ["competitors", "Competitors"], ["similar", "Similar contracts"],
    ["lifecycle", "Lifecycle"], ["funding", "Funding"], ["notes", "Notes"],
  ] as const;

  return (
    <div className="detail-layout">
      <PageHeader
        eyebrow="Διαδικασία 360"
        title={process.title ?? "(χωρίς τίτλο)"}
        subtitle={process.public_id}
        actions={
          <>
            <button className="button button-primary" type="button" onClick={() => setReportOpen(true)}>
              <FileCheck2 size={16} aria-hidden="true" />
              BID / NO-BID
            </button>
            {primaryPublication?.official_url && (
              <a className="button button-secondary" href={primaryPublication.official_url} target="_blank" rel="noreferrer">
                <ExternalLink size={16} aria-hidden="true" />
                Επίσημη σελίδα
              </a>
            )}
            {primaryPublication?.document_url && (
              <a className="button button-ghost" href={primaryPublication.document_url} target="_blank" rel="noreferrer">
                <Download size={16} aria-hidden="true" />
                Έγγραφο
              </a>
            )}
            <EvidenceDrawer objectType="procurement_processes" objectId={id} />
            <BackLink />
          </>
        }
      >
        <div className="badge-row">
          <Badge tone="green">{process.lifecycle_status}</Badge>
          <Badge>{process.record_status}</Badge>
          {process.currency && <Badge tone="blue">{process.currency}</Badge>}
        </div>
      </PageHeader>
      {reportOpen && <BidReportPanel processId={id} onClose={() => setReportOpen(false)} />}

      <div className="metric-grid">
        <MetricCard label="Εκτίμηση" value={formatAmount(process.estimated_value, process.currency)} icon={Landmark} />
        <MetricCard label="Ανάθεση" value={formatAmount(process.awarded_value, process.currency)} icon={ReceiptText} tone="green" />
        <MetricCard
          label="Τρέχουσα σύμβαση"
          value={formatAmount(process.current_contract_value, process.currency)}
          icon={CalendarClock}
          tone="amber"
        />
        <MetricCard label="Γεωγραφία" value={process.locations.length} detail="NUTS / περιοχές εκτέλεσης" icon={MapPinned} />
        {score && <MetricCard label="Opportunity score" value={Math.round(Number(score.score ?? 0))} detail="tenant-specific fit" icon={Radar} tone="blue" />}
      </div>

      <nav className="detail-tabs" aria-label="Ενότητες ευκαιρίας">
        {tabs.map(([value, label]) => <button key={value} type="button" className={activeTab === value ? "is-active" : ""} aria-current={activeTab === value ? "page" : undefined} onClick={() => setActiveTab(value)}>{label}</button>)}
      </nav>

      <div hidden={activeTab !== "overview"} className="detail-tab-panel">
      <TenderSummarySection summary={process.summary} />
      <div className="two-column">
        <Section title="Αναθέτουσα αρχή">
          {process.buyer?.entity_id ? (
            <KeyValueList>
              <KeyValue
                label="Όνομα"
                value={
                  <Link href={`/buyers/${process.buyer.entity_id}`} className="text-link">
                    {process.buyer.name}
                  </Link>
                }
              />
              <KeyValue label="ΑΦΜ" value={process.buyer.vat} />
              <KeyValue label="ΑΑΗΤ" value={process.buyer.aaht} />
            </KeyValueList>
          ) : (
            <EmptyState title="Δεν υπάρχει αναθέτουσα αρχή" />
          )}
        </Section>

        <Section title="Ποιότητα δεδομένων">
          <KeyValueList>
            {Object.keys(dataQuality).length > 0 ? (
              Object.entries(dataQuality).map(([key, value]) => <KeyValue key={key} label={key} value={valueText(value)} />)
            ) : (
              <KeyValue label="Κατάσταση" value="—" />
            )}
          </KeyValueList>
        </Section>
      </div>

      <Section title="Ανάδοχοι">
        {process.suppliers.length > 0 ? (
          <div className="table-shell">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Εταιρεία</th>
                  <th>Ρόλος</th>
                  <th>Ποσό</th>
                  <th>Τμήμα</th>
                </tr>
              </thead>
              <tbody>
                {process.suppliers.map((supplier) => (
                  <tr key={`${supplier.entity_id}-${supplier.lot_id ?? ""}`}>
                    <td>
                      <Link href={`/companies/${supplier.entity_id}`} className="table-link">
                        <Users size={15} aria-hidden="true" />
                        {supplier.name}
                      </Link>
                    </td>
                    <td>{supplier.party_role}</td>
                    <td>{formatAmount(supplier.amount, supplier.currency ?? process.currency)}</td>
                    <td>{supplier.lot_id ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="Δεν υπάρχουν ανάδοχοι" />
        )}
      </Section>
      <div className="two-column">
        <Section title="Γεωγραφία"><LocationList locations={process.locations} /></Section>
        <Section title="Κάλυψη εμπλουτισμού"><EnrichmentCoverage process={process} /></Section>
      </div></div>

      <div hidden={activeTab !== "competitors"} className="detail-tab-panel"><Section title="Ανταγωνιστικό τοπίο" eyebrow="Evidence-aware intelligence">
        {competitionQuery.query.isLoading && <LoadingState label="Ανάλυση συμμετοχών και incumbents" />}
        {competitionQuery.query.isError && <ErrorState title="Δεν είναι διαθέσιμο το ανταγωνιστικό τοπίο" error={competitionQuery.query.error} />}
        {competitionQuery.query.isSuccess && (
          <div className="process-competition">
            <div className="competition-column">
              <div className="competition-column-heading">
                <ShieldCheck size={16} aria-hidden="true" />
                <div><h3>Τεκμηριωμένα facts</h3><p>Επίσημη πηγή ή ρητός ρόλος με ΑΦΜ σε έγγραφο.</p></div>
              </div>
              <div className="competition-people">
                {competitionQuery.result.data.confirmed_participants.map((participant) => (
                  <CompetitionParticipantRow key={`${participant.company_id ?? participant.name}-${participant.role}`} participant={participant} />
                ))}
                {!competitionQuery.result.data.confirmed_participants.length && <EmptyState title="Δεν υπάρχουν τεκμηριωμένοι συμμετέχοντες" />}
              </div>
            </div>
            <div className="competition-column">
              <div className="competition-column-heading">
                <UsersRound size={16} aria-hidden="true" />
                <div><h3>Market intelligence</h3><p>Ιστορικό ίδιου φορέα ή CPV. Δεν αποτελεί δήλωση συμμετοχής.</p></div>
              </div>
              <div className="competition-people">
                {competitionQuery.result.data.likely_competitors.map((participant) => (
                  <CompetitionParticipantRow key={`${participant.company_id ?? participant.name}-${participant.role}`} participant={participant} />
                ))}
                {!competitionQuery.result.data.likely_competitors.length && <EmptyState title="Δεν υπάρχουν ακόμη επαρκή market signals" />}
              </div>
            </div>
            <p className="competition-coverage-note"><ShieldCheck size={14} /> {competitionQuery.result.data.coverage_note}</p>
          </div>
        )}
      </Section></div>

      <div hidden={activeTab !== "notes"} className="detail-tab-panel"><ObjectWorkspace objectType="procurement_processes" objectId={id} /></div>

      <div hidden={activeTab !== "bid"} className="detail-tab-panel">
        <BidWorkspacePanel processId={id} />
      </div>

      <div hidden={activeTab !== "lifecycle"} className="detail-tab-panel"><Section title="Χρονολόγιο">
        {timelineQuery.query.isLoading && <LoadingState label="Φόρτωση χρονολογίου" />}
        {timelineQuery.query.isError && <ErrorState error={timelineQuery.query.error} />}
        {timelineQuery.result && (
          <ol className="timeline">
            {timelineQuery.result.nodes.map((node) => {
              const identifier = node.identifiers.ADAM?.[0] ?? node.identifiers.ADA?.[0];

              return (
                <li key={node.act_id}>
                  <div className="timeline-dot" aria-hidden="true" />
                  <div className="timeline-content">
                    <div className="result-badges">
                      <Badge tone="blue">{node.act_type}</Badge>
                      {node.status && <Badge tone="green">{node.status}</Badge>}
                    </div>
                    <h3>{node.title ?? "(χωρίς τίτλο)"}</h3>
                    <div className="result-meta">
                      <span>{formatDate(node.event_date)}</span>
                      {node.amount_gross !== null && <span>{formatAmount(node.amount_gross, process.currency)}</span>}
                      {identifier && <span>{identifier}</span>}
                    </div>
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </Section></div>

      <div hidden={activeTab !== "documents"} className="detail-tab-panel">
      <ProcessDocumentTools processId={id} documents={process.documents} />
      <DocumentIntelligence processId={id} />
      <Section title="Επίσημες πηγές και αρχεία">
        <PublicationSources records={process.official_records} documents={process.documents} />
      </Section>
      <div className="two-column">
        <Section title="Πράξεις">
          <div className="compact-list">
            {process.acts.map((act) => {
              const identifier = act.identifiers.ADAM?.[0] ?? act.identifiers.ADA?.[0];
              const row = (
                <>
                  <Files size={16} aria-hidden="true" />
                  <span>{act.title ?? "(χωρίς τίτλο)"}</span>
                  <strong>{formatDate(act.publication_date ?? act.decision_date)}</strong>
                </>
              );

              return identifier ? (
                <Link key={act.act_id} href={`/contracts/${encodeURIComponent(identifier)}`} className="compact-row">
                  {row}
                </Link>
              ) : (
                <div key={act.act_id} className="compact-row">
                  {row}
                </div>
              );
            })}
            {process.acts.length === 0 && <EmptyState title="Δεν υπάρχουν πράξεις" />}
          </div>
        </Section>

        <Section title="Κάλυψη αρχείων">
          <KeyValueList>
            <KeyValue label="Επίσημες εγγραφές" value={process.official_records.length} />
            <KeyValue label="Ανακτημένα αρχεία" value={process.documents.length} />
            <KeyValue
              label="Κείμενο διαθέσιμο"
              value={process.documents.filter((document) => Boolean(document.excerpt)).length}
            />
          </KeyValueList>
        </Section>
      </div>

      <div className="two-column">
        <Section title="Διαύγεια">
          <GenericRecordList
            records={process.diavgeia_decisions}
            titleKeys={["subject", "title", "ada"]}
            metaKeys={["ada", "issue_date", "organization_name"]}
            emptyTitle="Δεν υπάρχουν αποφάσεις Διαύγειας"
          />
        </Section>

        <Section title="TED notices">
          <GenericRecordList records={process.ted_notices} titleKeys={["title", "ted_notice_id", "publication_number"]} metaKeys={["notice_type", "publication_date", "source"]} emptyTitle="Δεν υπάρχουν συνδεδεμένα TED notices" />
        </Section>
      </div></div>

      <div hidden={activeTab !== "funding"} className="detail-tab-panel"><div className="two-column">
        <Section title="Χρηματοδοτούμενα έργα"><GenericRecordList records={process.funding_projects} titleKeys={["title", "mis_ops_code"]} metaKeys={["program_period", "budget", "status", "confidence"]} emptyTitle="Δεν υπάρχουν συνδεδεμένα έργα χρηματοδότησης" /></Section>
        <Section title="Πρόσθετες πηγές">
          <GenericRecordList
            records={[...process.supplier_company_info, ...process.mef_expense_signals]}
            titleKeys={["official_name", "trade_name", "ted_notice_id", "title", "mis_ops_code", "mef_expense_id"]}
            metaKeys={["company_status", "program_period", "notice_type", "link_method", "confidence"]}
            emptyTitle="Δεν υπάρχουν πρόσθετες εγγραφές"
          />
        </Section>
      </div></div>

      <div hidden={activeTab !== "buyer"} className="detail-tab-panel">
        <Section title="Buyer history" eyebrow="Canonical buyer intelligence">
          {process.buyer?.entity_id ? <div className="buyer-history-callout"><Landmark size={22} /><div><strong>{process.buyer.name}</strong><p>Άνοιξε το πλήρες προφίλ για spend trend, CPV mix, top suppliers, concentration και renewal signals.</p></div><Link className="button button-secondary" href={`/buyers/${process.buyer.entity_id}`}>Προφίλ φορέα</Link></div> : <EmptyState title="Δεν έχει ταυτοποιηθεί φορέας" />}
        </Section>
      </div>

      <div hidden={activeTab !== "similar"} className="detail-tab-panel">
        <Section title="Similar contracts" eyebrow="CPV, buyer, title and value cohort">
          {similarQuery.query.isLoading && <LoadingState label="Σύγκριση συμβάσεων" />}
          {similarQuery.query.isError && <ErrorState title="Δεν είναι διαθέσιμες οι παρόμοιες συμβάσεις" error={similarQuery.query.error} />}
          {similarQuery.query.isSuccess && <div className="similar-contract-list">{similarQuery.result.data.map((item) => <Link href={`/processes/${item.process_id}`} key={item.process_id}><span><strong>{item.title ?? "Χωρίς τίτλο"}</strong><small>{item.buyer_name ?? "Άγνωστος φορέας"}</small></span><span className="badge-row">{item.reasons.slice(0, 2).map((reason) => <Badge key={reason}>{reason}</Badge>)}</span><strong>{formatAmount(item.contract_value, process.currency)}</strong><Badge tone="blue">{Math.round(item.similarity_score * 100)}%</Badge></Link>)}{!similarQuery.result.data.length && <EmptyState title="Δεν βρέθηκαν συγκρίσιμες συμβάσεις" />}</div>}
        </Section>
      </div>
    </div>
  );
}

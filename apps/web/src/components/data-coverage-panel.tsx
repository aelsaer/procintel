"use client";

import { useCustom } from "@refinedev/core";
import { Activity, ArrowRight, CheckCircle2, Clock3, Database, Gauge, RefreshCw, ShieldCheck, TriangleAlert, Workflow } from "lucide-react";
import { Badge, ErrorState, LoadingState } from "@/components/procurement-ui";
import type { DataCoverageResponse } from "@/lib/api";
import { SourceOperations } from "@/components/source-operations";

const SOURCE_LABELS: Record<string, string> = {
  KHMDHS: "ΚΗΜΔΗΣ",
  DIAVGEIA: "Διαύγεια",
  GEMI: "ΓΕΜΗ",
  TED: "TED",
  ANAPTYXI: "ΑΝΑΠΤΥΞΗ",
  MEF: "ΜΕΦ",
  DOCUMENTS: "Έγγραφα",
  GEOCODING: "Geocoding",
  CANONICAL: "Canonical store",
  LIFECYCLE: "Lifecycle",
  SUPPLIERS: "Προμηθευτές",
  ACTS: "Πράξεις",
  INSPIRE: "INSPIRE",
  INSPIRE_GISCO: "Eurostat GISCO",
  KTIMATOLOGIO_INSPIRE: "Κτηματολόγιο INSPIRE",
};

function label(value: string): string {
  return SOURCE_LABELS[value] ?? value;
}

function number(value: number | undefined): string {
  return new Intl.NumberFormat("el-GR", { notation: "compact", maximumFractionDigits: 1 }).format(value ?? 0);
}

function dateTime(value: string | null): string {
  if (!value) return "σε εξέλιξη";
  return new Intl.DateTimeFormat("el-GR", { dateStyle: "short", timeStyle: "short" }).format(new Date(value));
}

function tone(status: string): "green" | "blue" | "amber" | "red" | "neutral" {
  if (["CONNECTED", "SUCCEEDED", "HEALTHY"].includes(status)) return "green";
  if (status === "RUNNING") return "blue";
  if (["PARTIAL", "DEGRADED", "LOADED_UNLINKED"].includes(status)) return "amber";
  if (["FAILED", "STALE", "UNAVAILABLE", "BLOCKED_UPSTREAM"].includes(status)) return "red";
  return "neutral";
}

function statusLabel(status: string): string {
  return ({
    CONNECTED: "συνδεδεμένο",
    LOADED_UNLINKED: "φορτωμένο · ασύνδετο",
    NOT_LOADED: "χωρίς δεδομένα",
    RUNNING: "τρέχει",
    SUCCEEDED: "ολοκληρώθηκε",
    PARTIAL: "μερικό",
    FAILED: "απέτυχε",
    BLOCKED_UPSTREAM: "μη διαθέσιμο upstream",
    HEALTHY: "εντός στόχου",
    DEGRADED: "χρειάζεται έλεγχο",
    STALE: "εκτός freshness SLO",
    UNAVAILABLE: "μη διαθέσιμο",
  } as Record<string, string>)[status] ?? status;
}

function duration(seconds: number | null): string {
  if (seconds === null) return "χωρίς επιτυχή ανάκτηση";
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} λεπτά`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} ώρες`;
  return `${Math.round(seconds / 86400)} ημέρες`;
}

export function DataCoveragePanel({ compact = false, onOpen }: { compact?: boolean; onOpen?: () => void }) {
  const response = useCustom<DataCoverageResponse>({
    url: "/v1/analytics/data-coverage",
    method: "get",
    queryOptions: { retry: 1, refetchInterval: 15_000 },
  });
  const data = response.query.isSuccess ? response.result.data : null;

  if (compact) {
    if (response.query.isLoading) return <LoadingState label="Έλεγχος εισαγωγών" />;
    if (response.query.isError) return <ErrorState title="Δεν είναι διαθέσιμη η κατάσταση εισαγωγής" error={response.query.error} />;
    if (!data) return null;
    const healthy = data.assessments.filter((item) => item.status === "HEALTHY").length;
    const sourceCount = data.sources.filter((source) => source.source_system !== "TEST").length;
    return (
      <section className="ingestion-status-strip" aria-label="Κατάσταση εισαγωγής δεδομένων">
        <div><span className="live-dot" /><span><strong>{number(data.totals.acts)}</strong><small>canonical πράξεις</small></span></div>
        <div><Database size={16} /><span><strong>{sourceCount}</strong><small>πηγές με δεδομένα</small></span></div>
        <div><ShieldCheck size={16} /><span><strong>{healthy}/{data.assessments.length}</strong><small>πηγές εντός SLO</small></span></div>
        {onOpen ? <button type="button" onClick={onOpen}>Πηγές και ροές <ArrowRight size={15} /></button> : null}
      </section>
    );
  }

  return (
    <section className="data-coverage-panel" aria-labelledby="data-coverage-title">
      <header className="panel-heading">
        <div><span className="eyebrow">Live ingestion</span><h2 id="data-coverage-title">Πηγές και συνδέσεις δεδομένων</h2></div>
        <button className="icon-button" type="button" onClick={() => response.query.refetch()} aria-label="Ανανέωση κατάστασης εισαγωγής"><RefreshCw size={16} /></button>
      </header>
      {response.query.isLoading ? <LoadingState label="Ανάγνωση εισαγωγών και συνδέσεων" /> : null}
      {response.query.isError ? <ErrorState title="Δεν είναι διαθέσιμη η κατάσταση εισαγωγής" error={response.query.error} /> : null}
      {data ? <>
        <section className="completeness-trust" aria-labelledby="completeness-trust-title">
          <div className="completeness-trust-summary">
            <div>
              <span className="eyebrow">Coverage assurance</span>
              <h3 id="completeness-trust-title">Τι γνωρίζουμε ότι είναι πλήρες</h3>
              <p>Κάθε πηγή βαθμολογείται ως προς ingestion, canonical σύνδεση, έγγραφα, parties και freshness. Η ένδειξη «παρατηρούμενη» δεν παρουσιάζεται ως επαληθευμένο upstream σύνολο.</p>
            </div>
            <div className="completeness-summary-metrics">
              <span><strong>{data.assessments.filter((item) => item.status === "HEALTHY").length}/{data.assessments.length}</strong><small>εντός SLO</small></span>
              <span><strong>{data.assessments.filter((item) => item.claim_level === "VERIFIED_WINDOW").length}</strong><small>verified windows</small></span>
              <span><strong>{Math.round(data.assessments.reduce((sum, item) => sum + item.score, 0) / Math.max(data.assessments.length, 1))}%</strong><small>weighted coverage</small></span>
            </div>
          </div>
          <div className="completeness-source-grid">
            {data.assessments.map((assessment) => (
              <article key={assessment.source_system}>
                <header>
                  <span><strong>{label(assessment.source_system)}</strong><small>{assessment.claim_level === "VERIFIED_WINDOW" ? "Επαληθευμένο παράθυρο" : "Παρατηρούμενη κάλυψη"}</small></span>
                  <Badge tone={tone(assessment.status)}>{statusLabel(assessment.status)}</Badge>
                </header>
                <div className="completeness-score">
                  <Gauge size={15} /><strong>{Math.round(assessment.score)}%</strong>
                  <span><i style={{ width: `${Math.max(2, assessment.score)}%` }} /></span>
                </div>
                <div className="completeness-facts">
                  <span><Database size={13} />{number(assessment.observed_records)} records</span>
                  <span><Clock3 size={13} />{duration(assessment.freshness_seconds)}</span>
                  {assessment.pending_enrichments > 0 && <span><RefreshCw size={13} />{number(assessment.pending_enrichments)} pending</span>}
                </div>
                {assessment.findings[0] && <p title={assessment.findings.map((finding) => finding.message).join("\n")}><TriangleAlert size={13} />{assessment.findings[0].message}</p>}
              </article>
            ))}
          </div>
        </section>
        <div className="coverage-totals" aria-label="Σύνολα canonical βάσης">
          <div><Database size={17} /><span><strong>{number(data.totals.source_records)}</strong><small>source records</small></span></div>
          <div><Workflow size={17} /><span><strong>{number(data.totals.processes)}</strong><small>διαδικασίες</small></span></div>
          <div><Activity size={17} /><span><strong>{number(data.totals.acts)}</strong><small>canonical πράξεις</small></span></div>
          <div><CheckCircle2 size={17} /><span><strong>{number(data.totals.precise_locations)}</strong><small>ακριβείς τοποθεσίες</small></span></div>
          <div><RefreshCw size={17} /><span><strong>{number(data.totals.pending_enrichments)}</strong><small>enrichments σε ουρά</small></span></div>
          <div><TriangleAlert size={17} /><span><strong>{number(data.totals.open_data_quality_issues)}</strong><small>ανοιχτοί έλεγχοι ποιότητας</small></span></div>
        </div>

        <div className="coverage-layout">
          <section className="source-coverage-list" aria-labelledby="source-coverage-title">
            <div className="coverage-section-heading"><h3 id="source-coverage-title">Φορτωμένες πηγές</h3><small>{dateTime(data.generated_at)}</small></div>
            {data.sources.filter((source) => source.source_system !== "TEST").map((source) => <article key={source.source_system}>
              <span className="source-code">{label(source.source_system)}</span>
              <span><strong>{number(source.record_count)}</strong><small>{source.failed_count ? `${number(source.failed_count)} parse failures` : "parsed"}</small></span>
              <span className="source-resources">{source.resources.slice(0, 4).map((resource) => <Badge key={resource.resource_type}>{resource.resource_type} · {number(resource.record_count)}</Badge>)}</span>
              <time dateTime={source.latest_fetched_at ?? undefined}>{dateTime(source.latest_fetched_at)}</time>
            </article>)}
          </section>

          <section className="connection-coverage-list" aria-labelledby="connection-coverage-title">
            <div className="coverage-section-heading"><h3 id="connection-coverage-title">Cross-source συνδέσεις</h3><small>{number(data.totals.act_links)} act links</small></div>
            {data.connections.map((connection) => <article key={`${connection.source}-${connection.target}-${connection.relation}`}>
              <span>{label(connection.source)}</span>
              <span className="connection-relation"><small>{connection.relation}</small><ArrowRight size={14} /></span>
              <span>{label(connection.target)}</span>
              <strong>{number(connection.linked_records)}</strong>
              <Badge tone={tone(connection.status)}>{statusLabel(connection.status)}</Badge>
            </article>)}
          </section>
        </div>

        <section className="connector-run-list" aria-labelledby="connector-run-title">
          <div className="coverage-section-heading"><h3 id="connector-run-title">Πρόσφατα ingestion windows</h3><small>τελευταίες 12 εκτελέσεις</small></div>
          <div role="table">
            <div role="row" className="connector-run-head"><span>Resource</span><span>Window</span><span>Read / new / same</span><span>Κατάσταση</span><span>Τέλος</span></div>
            {data.recent_runs.slice(0, 12).map((run) => <div role="row" key={`${run.source_system}-${run.resource_type}-${run.partition_key}-${run.started_at}`}>
              <span><strong>{label(run.source_system)}</strong><small>{run.resource_type}</small></span>
              <span title={run.partition_key}>{run.partition_key.split(":").slice(-2).join(" → ")}</span>
              <span>{number(run.records_fetched)} / {number(run.records_upserted)} / {number(run.records_unchanged)}</span>
              <Badge tone={tone(run.status)}>{statusLabel(run.status)}</Badge>
              <span>{dateTime(run.finished_at)}</span>
              {run.error ? <small className="connector-run-error"><TriangleAlert size={13} />{String(run.error.message ?? "record failures")}</small> : null}
            </div>)}
          </div>
        </section>
        <section className="dataset-validation-list" aria-labelledby="dataset-validation-title">
          <div className="coverage-section-heading"><h3 id="dataset-validation-title">CKAN dataset contracts</h3><small>{data.dataset_validations.length} validations</small></div>
          <div role="table">
            <div role="row"><strong>Dataset</strong><strong>Adapter</strong><strong>Schema</strong><strong>Status</strong><strong>Έλεγχος</strong></div>
            {data.dataset_validations.map((validation) => <div role="row" key={`${validation.dataset_name}-${validation.validated_at}`}>
              <a href={validation.resource_url} target="_blank" rel="noreferrer">{validation.dataset_name}</a>
              <span>{validation.adapter_name}</span>
              <span>{validation.detected_format ?? "unknown"} · {validation.columns.slice(0, 5).join(", ")}</span>
              <Badge tone={validation.status === "VALID" ? "green" : "red"}>{validation.status}</Badge>
              <time>{dateTime(validation.validated_at)}</time>
            </div>)}
          </div>
        </section>
        <SourceOperations />
      </> : null}
    </section>
  );
}

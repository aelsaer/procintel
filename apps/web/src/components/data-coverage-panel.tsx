"use client";

import { useCustom } from "@refinedev/core";
import { Activity, ArrowRight, CheckCircle2, Database, RefreshCw, TriangleAlert, Workflow } from "lucide-react";
import { Badge, ErrorState, LoadingState } from "@/components/procurement-ui";
import type { DataCoverageResponse } from "@/lib/api";

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
  if (["CONNECTED", "SUCCEEDED"].includes(status)) return "green";
  if (status === "RUNNING") return "blue";
  if (["PARTIAL", "LOADED_UNLINKED"].includes(status)) return "amber";
  if (status === "FAILED") return "red";
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
  } as Record<string, string>)[status] ?? status;
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
    const connected = data.connections.filter((item) => item.status === "CONNECTED").length;
    const sourceCount = data.sources.filter((source) => source.source_system !== "TEST").length;
    return (
      <section className="ingestion-status-strip" aria-label="Κατάσταση εισαγωγής δεδομένων">
        <div><span className="live-dot" /><span><strong>{number(data.totals.acts)}</strong><small>canonical πράξεις</small></span></div>
        <div><Database size={16} /><span><strong>{sourceCount}</strong><small>πηγές με δεδομένα</small></span></div>
        <div><Workflow size={16} /><span><strong>{connected}/{data.connections.length}</strong><small>ενεργές συνδέσεις</small></span></div>
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
        <div className="coverage-totals" aria-label="Σύνολα canonical βάσης">
          <div><Database size={17} /><span><strong>{number(data.totals.source_records)}</strong><small>source records</small></span></div>
          <div><Workflow size={17} /><span><strong>{number(data.totals.processes)}</strong><small>διαδικασίες</small></span></div>
          <div><Activity size={17} /><span><strong>{number(data.totals.acts)}</strong><small>canonical πράξεις</small></span></div>
          <div><CheckCircle2 size={17} /><span><strong>{number(data.totals.precise_locations)}</strong><small>ακριβείς τοποθεσίες</small></span></div>
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
            <div role="row" className="connector-run-head"><span>Resource</span><span>Window</span><span>Read / written</span><span>Κατάσταση</span><span>Τέλος</span></div>
            {data.recent_runs.slice(0, 12).map((run) => <div role="row" key={`${run.source_system}-${run.resource_type}-${run.partition_key}-${run.started_at}`}>
              <span><strong>{label(run.source_system)}</strong><small>{run.resource_type}</small></span>
              <span title={run.partition_key}>{run.partition_key.split(":").slice(-2).join(" → ")}</span>
              <span>{number(run.records_fetched)} / {number(run.records_upserted)}</span>
              <Badge tone={tone(run.status)}>{statusLabel(run.status)}</Badge>
              <span>{dateTime(run.finished_at)}</span>
              {run.error ? <small className="connector-run-error"><TriangleAlert size={13} />{String(run.error.message ?? "record failures")}</small> : null}
            </div>)}
          </div>
        </section>
      </> : null}
    </section>
  );
}

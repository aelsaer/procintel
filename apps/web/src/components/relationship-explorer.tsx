"use client";

import { useEffect, useMemo, useState } from "react";
import { useCustom } from "@refinedev/core";
import { keepPreviousData } from "@tanstack/react-query";
import Link from "next/link";
import { ArrowRight, ExternalLink, Filter, Network, Table2 } from "lucide-react";
import { Badge, EmptyState, ErrorState, LoadingState } from "@/components/procurement-ui";
import {
  activeCpvPrefixes,
  activeKeywords,
  businessScopeQuery,
  type BusinessScope,
} from "@/lib/business-scope";

type RelationshipResponse = {
  nodes: Array<{ id: string; node_type: string; label: string; value: number | string | null }>;
  edges: Array<{ source: string; target: string; relation_type: string; value: number | string | null; confidence: number; act_id: string | null; date: string | null; official_document_identifier: string | null; official_url: string | null; evidence: { document_url?: string | null } }>;
  table: Array<{ process_id: string; process: string | null; buyer: string | null; supplier: string | null; value: number | string | null; cpv_codes: string[]; official_document_identifier: string | null; official_url: string | null; document_url: string | null; date: string | null }>;
};

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debouncedValue, setDebouncedValue] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedValue(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [delayMs, value]);
  return debouncedValue;
}

export function RelationshipExplorer({ profile }: { profile: BusinessScope }) {
  const [mode, setMode] = useState<"graph" | "table">("graph");
  const [relationType, setRelationType] = useState("");
  const [minimumValue, setMinimumValue] = useState("");
  const [source, setSource] = useState("");
  const [confidence, setConfidence] = useState("0");
  const query = useMemo(() => {
    const scopeQuery = businessScopeQuery(profile);
    delete scopeQuery.amount_min;
    delete scopeQuery.reference_afm;
    return {
      ...scopeQuery,
      ...(relationType ? { relation_type: relationType } : {}),
      ...((minimumValue || Number(profile.amountMin) > 0)
        ? { minimum_value: minimumValue || profile.amountMin }
        : {}),
      ...(source ? { source } : {}),
      minimum_confidence: confidence,
      limit: 80,
    };
  }, [confidence, minimumValue, profile, relationType, source]);
  const debouncedQuery = useDebouncedValue(query, 300);
  const response = useCustom<RelationshipResponse>({
    url: "/v1/intelligence/relationships", method: "get",
    config: { query: debouncedQuery },
    queryOptions: { retry: 1, placeholderData: keepPreviousData },
  });
  const data = response.query.isSuccess ? response.result.data : null;
  const visibleNodes = data?.nodes.slice(0, 18) ?? [];
  const nodesById = new Map(data?.nodes.map((node) => [node.id, node]) ?? []);

  return (
    <section className="relationship-explorer" aria-labelledby="relationship-title">
      <div className="panel-heading"><div><span className="eyebrow">Canonical procurement graph</span><h2 id="relationship-title">Relationship Explorer</h2></div><div className="segmented-control"><button type="button" className={mode === "graph" ? "is-active" : ""} onClick={() => setMode("graph")}><Network size={14} />Graph</button><button type="button" className={mode === "table" ? "is-active" : ""} onClick={() => setMode("table")}><Table2 size={14} />Table</button></div></div>
      <div className="competition-scope">
        <Network size={15} aria-hidden="true" />
        <span>{activeCpvPrefixes(profile).length || "Όλα"} CPV · {activeKeywords(profile).join(", ") || "όλα τα αντικείμενα"} · {profile.dateFrom} - {profile.dateTo}</span>
        <Badge tone="blue">Ενεργό προφίλ</Badge>
      </div>
      <details className="relationship-filters">
        <summary><Filter size={14} /> Φίλτρα σχέσεων</summary>
        <div>
          <label><span>Σχέση</span><select value={relationType} onChange={(event) => setRelationType(event.target.value)}><option value="">Όλες</option><option value="PROCURES">Buyer → process</option><option value="AWARDED_TO">Process → supplier</option><option value="FUNDED_BY">Process → funding</option></select></label>
          <label><span>Ελάχιστη αξία</span><input inputMode="numeric" value={minimumValue} onChange={(event) => setMinimumValue(event.target.value.replace(/[^0-9.]/g, ""))} placeholder="0" /></label>
          <label><span>Πηγή</span><select value={source} onChange={(event) => setSource(event.target.value)}><option value="">Όλες</option><option value="canonical">Canonical buyer</option><option value="act_parties">Act parties</option><option value="funding_links">Funding links</option></select></label>
          <label><span>Confidence</span><select value={confidence} onChange={(event) => setConfidence(event.target.value)}><option value="0">Όλα</option><option value="0.65">≥ 65%</option><option value="0.85">≥ 85%</option><option value="0.95">≥ 95%</option></select></label>
        </div>
      </details>
      {response.query.isLoading && !data ? <LoadingState label="Σύνθεση σχέσεων" /> : null}
      {response.query.isFetching && data ? <p className="relationship-note" role="status">Ανανέωση σχέσεων...</p> : null}
      {response.query.isError ? <ErrorState title="Δεν είναι διαθέσιμο το γράφημα σχέσεων" error={response.query.error} /> : null}
      {data && mode === "graph" && <div className="relationship-graph-wrap"><div className="relationship-graph" aria-label="Κόμβοι αγοραστών, διαδικασιών, προμηθευτών και χρηματοδότησης">{visibleNodes.map((node) => <div key={node.id} className={`graph-node node-${node.node_type.toLowerCase()}`}><Badge>{node.node_type}</Badge><strong>{node.label}</strong></div>)}</div><div className="relationship-edges" aria-label="Συνδέσεις κόμβων">{data.edges.slice(0, 14).map((edge, index) => <div key={`${edge.source}-${edge.target}-${index}`}><span>{nodesById.get(edge.source)?.label ?? edge.source}</span><span><Badge>{edge.relation_type}</Badge><ArrowRight size={14} /></span><span>{nodesById.get(edge.target)?.label ?? edge.target}</span><small>{Math.round(edge.confidence * 100)}%{edge.official_url || edge.evidence.document_url ? <a href={edge.official_url ?? edge.evidence.document_url ?? "#"} target="_blank" rel="noreferrer" title={edge.official_document_identifier ?? "Επίσημο evidence"}><ExternalLink size={12} /></a> : null}</small></div>)}</div></div>}
      {mode === "table" && <div className="relationship-table" role="table" aria-busy={response.query.isFetching}><div role="row"><strong>Αγοραστής</strong><strong>Διαδικασία</strong><strong>Προμηθευτής</strong><strong>Αξία</strong><strong>Evidence</strong></div>{(data?.table ?? []).slice(0, 20).map((row) => <div role="row" key={`${row.process_id}-${row.supplier}`}><span>{row.buyer ?? "-"}</span><Link href={`/processes/${row.process_id}`}>{row.process ?? row.process_id}</Link><span>{row.supplier ?? "-"}</span><span>{new Intl.NumberFormat("el-GR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(Number(row.value ?? 0))}</span><span>{row.official_url || row.document_url ? <a href={row.official_url ?? row.document_url ?? "#"} target="_blank" rel="noreferrer">{row.official_document_identifier ?? "Έγγραφο"} <ExternalLink size={12} /></a> : "—"}</span></div>)}</div>}
      {data && !data.nodes.length ? <EmptyState title="Δεν υπάρχουν σχέσεις στο ενεργό προφίλ" detail="Δεν αναμιγνύονται σχέσεις από τη συνολική αγορά. Διεύρυνε CPV, λέξεις-κλειδιά ή χρονικό εύρος στο Προφίλ." /> : null}
      <p className="relationship-note">Κάθε edge εμφανίζει ημερομηνία, επίσημο αναγνωριστικό και διαθέσιμο τεκμήριο. Η table view παραμένει η ακριβής προσβάσιμη εναλλακτική.</p>
    </section>
  );
}

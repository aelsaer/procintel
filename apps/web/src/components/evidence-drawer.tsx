"use client";

import { useState } from "react";
import { Database, ExternalLink, Info, X } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Badge, EmptyState, LoadingState } from "@/components/procurement-ui";

type EvidenceResponse = {
  object_type: string;
  object_id: string;
  freshness: string | null;
  minimum_confidence: number | null;
  fields: Array<{ field_name: string; source: string; source_native_id: string | null; source_path: string | null; extraction_method: string; confidence: number; observed_at: string; retrieved_at: string; license_code: string | null; source_record_id: string }>;
};

type MethodologyResponse = {
  metric: string;
  label: string;
  formula: string;
  value_basis: string;
  minimum_sample: number;
  limitations: string[];
  source_tables: string[];
};

export function EvidenceDrawer({ objectType, objectId, label = "Evidence" }: { objectType: string; objectId: string; label?: string }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<EvidenceResponse | null>(null);
  const [loading, setLoading] = useState(false);

  async function show() {
    setOpen(true);
    if (data) return;
    setLoading(true);
    try { setData(await apiFetch<EvidenceResponse>(`/v1/evidence/${objectType}/${objectId}`)); }
    finally { setLoading(false); }
  }

  return <>
    <button className="button button-secondary" type="button" onClick={() => void show()}><Info size={15} />{label}</button>
    {open ? <div className="evidence-backdrop" role="presentation" onMouseDown={() => setOpen(false)}><aside className="evidence-drawer" role="dialog" aria-modal="true" aria-labelledby="evidence-title" onMouseDown={(event) => event.stopPropagation()}><header><div><span className="eyebrow">Field-level provenance</span><h2 id="evidence-title">Τεκμηρίωση δεδομένων</h2></div><button className="icon-button" type="button" onClick={() => setOpen(false)} aria-label="Κλείσιμο"><X size={17} /></button></header>{loading ? <LoadingState label="Ανάγνωση πηγών" /> : null}{data ? <><div className="evidence-summary"><span><Database size={15} />{data.fields.length} field references</span><span>{data.freshness ? new Intl.DateTimeFormat("el-GR", { dateStyle: "medium" }).format(new Date(data.freshness)) : "χωρίς freshness"}</span>{data.minimum_confidence !== null ? <Badge tone="blue">{Math.round(data.minimum_confidence * 100)}% min confidence</Badge> : null}</div><div className="evidence-field-list">{data.fields.map((field, index) => <article key={`${field.field_name}-${field.source_record_id}-${index}`}><div><strong>{field.field_name}</strong><Badge>{field.source}</Badge></div><p>{field.extraction_method}{field.source_path ? ` · ${field.source_path}` : ""}</p><small>{field.source_native_id ?? field.source_record_id} · {Math.round(field.confidence * 100)}% · {field.license_code ?? "license not recorded"}</small></article>)}</div>{!data.fields.length ? <EmptyState title="Δεν υπάρχουν field references" detail="Η εγγραφή παραμένει διαθέσιμη από το canonical source record, αλλά δεν έχει ακόμη field-level mapping." /> : null}<p className="evidence-footnote"><ExternalLink size={13} /> Κάθε reference διατηρεί source record, extraction method, confidence και χρόνο παρατήρησης.</p></> : null}</aside></div> : null}
  </>;
}

export function MetricMethodologyDrawer({ metric, label = "Μεθοδολογία" }: { metric: string; label?: string }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<MethodologyResponse | null>(null);
  const [loading, setLoading] = useState(false);

  async function show() {
    setOpen(true);
    if (data) return;
    setLoading(true);
    try { setData(await apiFetch<MethodologyResponse>(`/v1/evidence/methodologies/${metric}`)); }
    finally { setLoading(false); }
  }

  return <>
    <button className="button button-secondary methodology-button" type="button" onClick={() => void show()}><Info size={14} />{label}</button>
    {open && <div className="evidence-backdrop" role="presentation" onMouseDown={() => setOpen(false)}><aside className="evidence-drawer" role="dialog" aria-modal="true" aria-labelledby="methodology-title" onMouseDown={(event) => event.stopPropagation()}><header><div><span className="eyebrow">Metric evidence</span><h2 id="methodology-title">{data?.label ?? label}</h2></div><button className="icon-button" type="button" onClick={() => setOpen(false)} aria-label="Κλείσιμο"><X size={17} /></button></header>{loading && <LoadingState label="Ανάγνωση μεθοδολογίας" />}{data && <div className="methodology-content"><div><span>Τύπος</span><strong>{data.formula}</strong></div><div><span>Βάση αξίας</span><strong>{data.value_basis}</strong></div><div><span>Ελάχιστο δείγμα</span><strong>{data.minimum_sample}</strong></div><div><span>Πηγές</span><strong>{data.source_tables.join(" · ")}</strong></div><h3>Περιορισμοί</h3><ul>{data.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul></div>}</aside></div>}
  </>;
}

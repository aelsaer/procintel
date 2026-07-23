"use client";

import { useEffect, useState } from "react";
import { Download, FileSpreadsheet, RefreshCw } from "lucide-react";
import { api, API_BASE_URL, type ExportJobResponse } from "@/lib/api";
import { Badge, EmptyState } from "@/components/procurement-ui";
import {
  activeCpvPrefixes,
  activeKeywords,
  businessScopeQuery,
  type BusinessScope,
} from "@/lib/business-scope";

export function ExportsPanel({ profile }: { profile: BusinessScope }) {
  const [jobs, setJobs] = useState<ExportJobResponse[]>([]);
  const [busy, setBusy] = useState(false);

  async function refresh() { setJobs(await api.getExports()); }
  useEffect(() => {
    let active = true;
    void api.getExports().then((items) => { if (active) setJobs(items); });
    return () => { active = false; };
  }, []);

  async function create(format: "CSV" | "XLSX") {
    setBusy(true);
    try {
      const filters = businessScopeQuery(profile);
      delete filters.reference_afm;
      await api.createExport("OPPORTUNITIES", format, filters);
      await refresh();
      window.setTimeout(() => void refresh(), 1200);
    } finally { setBusy(false); }
  }

  return (
    <section className="exports-panel" aria-labelledby="exports-title">
      <div className="panel-heading"><div><span className="eyebrow">Reproducible exports</span><h2 id="exports-title">CSV / XLSX</h2></div><div className="row-actions"><button className="button button-secondary" type="button" disabled={busy} onClick={() => void create("CSV")}><Download size={15} />CSV</button><button className="button button-secondary" type="button" disabled={busy} onClick={() => void create("XLSX")}><FileSpreadsheet size={15} />XLSX</button><button className="icon-button" type="button" onClick={() => void refresh()} aria-label="Ανανέωση exports"><RefreshCw size={15} /></button></div></div>
      <div className="competition-scope">
        <FileSpreadsheet size={15} aria-hidden="true" />
        <span>{activeCpvPrefixes(profile).length || "Όλα"} CPV · {activeKeywords(profile).join(", ") || "όλα τα αντικείμενα"} · {profile.dateFrom} - {profile.dateTo}</span>
        <Badge tone="blue">Ενεργό προφίλ</Badge>
      </div>
      <div className="export-job-list">
        {jobs.slice(0, 6).map((job) => <div key={job.id}><span><strong>{job.file_name ?? `${job.export_type} ${job.format}`}</strong><small>{new Intl.DateTimeFormat("el-GR", { dateStyle: "medium", timeStyle: "short" }).format(new Date(job.created_at))}{job.row_count !== null ? ` · ${job.row_count} γραμμές` : ""}</small></span><Badge tone={job.status === "SUCCEEDED" ? "green" : job.status === "FAILED" ? "amber" : "blue"}>{job.status}</Badge>{job.download_url ? <a className="icon-button" href={`${API_BASE_URL}${job.download_url}`} aria-label="Λήψη export"><Download size={15} /></a> : null}</div>)}
        {!jobs.length ? <EmptyState title="Δεν υπάρχουν exports" detail="Το export διατηρεί τα ενεργά φίλτρα και το source attribution." /> : null}
      </div>
    </section>
  );
}

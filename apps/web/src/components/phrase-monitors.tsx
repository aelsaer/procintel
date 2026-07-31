"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { FileSearch, Play, Plus, Trash2 } from "lucide-react";

import { Badge, EmptyState } from "@/components/procurement-ui";
import { apiFetch, type PhraseMonitorResponse } from "@/lib/api";
import { activeCpvPrefixes, type BusinessScope } from "@/lib/business-scope";

export function PhraseMonitors({ profile }: { profile: BusinessScope }) {
  const [monitors, setMonitors] = useState<PhraseMonitorResponse[]>([]);
  const [name, setName] = useState("Όροι ενδιαφέροντος");
  const [phrases, setPhrases] = useState("");
  const [mode, setMode] = useState<"ANY" | "ALL" | "EXACT">("ANY");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function reload() {
    setMonitors(await apiFetch<PhraseMonitorResponse[]>("/v1/document-tools/phrase-monitors"));
  }

  useEffect(() => {
    let active = true;
    void apiFetch<PhraseMonitorResponse[]>("/v1/document-tools/phrase-monitors")
      .then((rows) => { if (active) setMonitors(rows); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Δεν φορτώθηκαν τα phrase monitors."); });
    return () => { active = false; };
  }, []);

  async function run(operation: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try { await operation(); } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Η ενέργεια απέτυχε.");
    } finally { setBusy(false); }
  }

  function create(event: FormEvent) {
    event.preventDefault();
    const values = phrases.split(/[\n,;]/).map((value) => value.trim()).filter(Boolean);
    if (!values.length) return;
    void run(async () => {
      await apiFetch("/v1/document-tools/phrase-monitors", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          phrases: values,
          match_mode: mode,
          cpv_prefixes: activeCpvPrefixes(profile),
          is_active: true,
        }),
      });
      setPhrases("");
      await reload();
    });
  }

  return (
    <section className="phrase-monitor-panel" aria-labelledby="phrase-monitor-title">
      <div className="panel-heading"><div><span className="eyebrow">Inside official documents</span><h2 id="phrase-monitor-title">Phrase monitoring</h2></div><FileSearch size={18} /></div>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      <form onSubmit={create}>
        <input value={name} onChange={(event) => setName(event.target.value)} aria-label="Όνομα phrase monitor" />
        <input value={phrases} onChange={(event) => setPhrases(event.target.value)} placeholder="π.χ. GIS, ISO 27001, προαίρεση" aria-label="Φράσεις προς παρακολούθηση" />
        <select value={mode} onChange={(event) => setMode(event.target.value as typeof mode)} aria-label="Τρόπος αντιστοίχισης"><option value="ANY">Οποιαδήποτε</option><option value="ALL">Όλες</option><option value="EXACT">Ακριβής φράση</option></select>
        <button className="button button-primary" type="submit" disabled={busy || !phrases.trim()}><Plus size={15} />Monitor</button>
      </form>
      <div className="phrase-monitor-list">
        {monitors.map((monitor) => (
          <article key={monitor.id}>
            <header><span><strong>{monitor.name}</strong><small>{monitor.phrases.join(" · ")} · {monitor.match_mode}</small></span><Badge tone={monitor.match_count ? "green" : "neutral"}>{monitor.match_count} matches</Badge><button className="icon-button" type="button" title="Έλεγχος τώρα" disabled={busy} onClick={() => void run(async () => { await apiFetch(`/v1/document-tools/phrase-monitors/${monitor.id}/evaluate`, { method: "POST" }); await reload(); })}><Play size={14} /></button><button className="icon-button is-danger" type="button" title="Διαγραφή" disabled={busy} onClick={() => void run(async () => { await apiFetch(`/v1/document-tools/phrase-monitors/${monitor.id}`, { method: "DELETE" }); await reload(); })}><Trash2 size={14} /></button></header>
            {monitor.matches.slice(0, 4).map((match) => match.process_id ? <Link href={`/processes/${match.process_id}`} key={match.id}><span>{match.process_title ?? match.document_title ?? "Document match"}</span><small>σελ. {match.page_numbers.join(", ")} · {match.matched_phrases.join(", ")}</small></Link> : null)}
          </article>
        ))}
        {!monitors.length ? <EmptyState title="Δεν υπάρχουν phrase monitors" detail="Παρακολουθήστε ακριβείς όρους μέσα στα εξαγμένα κείμενα των επίσημων εγγράφων." /> : null}
      </div>
    </section>
  );
}

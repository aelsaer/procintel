"use client";

import { useEffect, useMemo, useState } from "react";
import {
  BookOpenCheck,
  Check,
  Download,
  ExternalLink,
  FileClock,
  FilePenLine,
  Library,
  Loader2,
  Plus,
  Save,
  Sparkles,
  Trash2,
} from "lucide-react";

import { Badge, EmptyState } from "@/components/procurement-ui";
import {
  ApiError,
  apiFetch,
  downloadApiFile,
  type BidContentResponse,
  type ProposalSectionResponse,
  type ProposalVersionResponse,
  type ProposalWorkspaceResponse,
} from "@/lib/api";

type Mode = "draft" | "library";

function citationValue(citation: Record<string, unknown>, key: string): string | null {
  const value = citation[key];
  return value === null || value === undefined ? null : String(value);
}

export function ProposalProduction({ processId }: { processId: string }) {
  const [mode, setMode] = useState<Mode>("draft");
  const [proposal, setProposal] = useState<ProposalWorkspaceResponse | null>(null);
  const [library, setLibrary] = useState<BidContentResponse[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [versions, setVersions] = useState<ProposalVersionResponse[]>([]);
  const [showVersions, setShowVersions] = useState(false);
  const [contentTitle, setContentTitle] = useState("");
  const [contentBody, setContentBody] = useState("");
  const [contentTags, setContentTags] = useState("");

  async function reload() {
    const [nextProposal, nextLibrary] = await Promise.all([
      apiFetch<ProposalWorkspaceResponse>(`/v1/proposals/process/${processId}`),
      apiFetch<BidContentResponse[]>("/v1/proposals/library"),
    ]);
    setProposal(nextProposal);
    setLibrary(nextLibrary);
    const preferred = nextProposal.sections.find((section) => section.id === selectedId) ?? nextProposal.sections[0];
    setSelectedId(preferred?.id ?? null);
    setBody(preferred?.body ?? "");
  }

  useEffect(() => {
    let active = true;
    void Promise.all([
      apiFetch<ProposalWorkspaceResponse>(`/v1/proposals/process/${processId}`),
      apiFetch<BidContentResponse[]>("/v1/proposals/library"),
    ]).then(([nextProposal, nextLibrary]) => {
      if (!active) return;
      setProposal(nextProposal);
      setLibrary(nextLibrary);
      setSelectedId(nextProposal.sections[0]?.id ?? null);
      setBody(nextProposal.sections[0]?.body ?? "");
    }).catch((reason: unknown) => {
      if (!active) return;
      if (!(reason instanceof ApiError && reason.status === 404)) {
        setError(reason instanceof Error ? reason.message : "Δεν φορτώθηκε η παραγωγή προσφοράς.");
      }
    });
    return () => { active = false; };
  }, [processId]);

  const selected = useMemo(
    () => proposal?.sections.find((section) => section.id === selectedId) ?? null,
    [proposal, selectedId],
  );

  async function action(operation: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await operation();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Η ενέργεια δεν ολοκληρώθηκε.");
    } finally {
      setBusy(false);
    }
  }

  function chooseSection(section: ProposalSectionResponse) {
    setSelectedId(section.id);
    setBody(section.body);
    setShowVersions(false);
    setVersions([]);
  }

  async function updateSection(values: Record<string, unknown>) {
    if (!selected) return;
    const updated = await apiFetch<ProposalSectionResponse>(`/v1/proposals/sections/${selected.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values),
    });
    setProposal((current) => current ? {
      ...current,
      approved_sections: current.sections.reduce(
        (count, section) => count + ((section.id === updated.id ? updated : section).status === "APPROVED" ? 1 : 0),
        0,
      ),
      sections: current.sections.map((section) => section.id === updated.id ? updated : section),
    } : current);
    setBody(updated.body);
  }

  return (
    <section className="proposal-production" aria-labelledby="proposal-production-title">
      <header className="proposal-header">
        <div>
          <span>Evidence-grounded production</span>
          <h3 id="proposal-production-title">Τεχνική προσφορά</h3>
        </div>
        <div className="proposal-header-actions">
          <div className="segmented-control" aria-label="Προβολή παραγωγής προσφοράς">
            <button type="button" className={mode === "draft" ? "is-active" : ""} onClick={() => setMode("draft")}><FilePenLine size={14} />Απαντήσεις</button>
            <button type="button" className={mode === "library" ? "is-active" : ""} onClick={() => setMode("library")}><Library size={14} />Βιβλιοθήκη</button>
          </div>
          <button
            className="button button-secondary"
            type="button"
            disabled={busy || !proposal?.sections.length}
            onClick={() => void action(() => downloadApiFile(`/v1/proposals/process/${processId}/export.docx`, "proposal.docx"))}
          >
            <Download size={15} />Word
          </button>
        </div>
      </header>
      {error ? <p className="form-error" role="alert">{error}</p> : null}

      {mode === "draft" ? (
        <>
          <div className="proposal-progress">
            <span><strong>{proposal?.requirements_mapped ?? 0}/{proposal?.requirements_total ?? 0}</strong> απαιτήσεις με απάντηση</span>
            <span><strong>{proposal?.approved_sections ?? 0}</strong> εγκεκριμένες</span>
            <button
              className="button button-primary"
              type="button"
              disabled={busy || !proposal?.requirements_total}
              onClick={() => void action(async () => {
                const generated = await apiFetch<ProposalWorkspaceResponse>(`/v1/proposals/process/${processId}/generate`, {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ language: "el-GR" }),
                });
                setProposal(generated);
                const first = generated.sections[0];
                setSelectedId(first?.id ?? null);
                setBody(first?.body ?? "");
              })}
            >
              {busy ? <Loader2 className="spin" size={15} /> : <Sparkles size={15} />}
              {proposal?.sections.length ? "Επαναδημιουργία" : "Δημιουργία πρώτου draft"}
            </button>
          </div>

          {proposal?.sections.length ? (
            <div className="proposal-editor-layout">
              <nav className="proposal-section-list" aria-label="Απαντήσεις απαιτήσεων">
                {proposal.sections.map((section) => (
                  <button type="button" key={section.id} className={selectedId === section.id ? "is-active" : ""} onClick={() => chooseSection(section)}>
                    <span>{section.title}</span>
                    <small>v{section.current_version} · {section.status.replace("_", " ")}</small>
                  </button>
                ))}
              </nav>
              {selected ? (
                <div className="proposal-editor">
                  <div className="proposal-editor-toolbar">
                    <select
                      aria-label="Κατάσταση απάντησης"
                      value={selected.status}
                      onChange={(event) => void action(() => updateSection({ status: event.target.value, change_summary: "Review status updated" }))}
                    >
                      <option value="DRAFT">Draft</option>
                      <option value="IN_REVIEW">In review</option>
                      <option value="NEEDS_CHANGES">Needs changes</option>
                      <option value="APPROVED">Approved</option>
                    </select>
                    <Badge tone={selected.generation_metadata.llm_used ? "blue" : "amber"}>
                      {selected.generation_metadata.llm_used ? "AI first draft" : "Deterministic draft"}
                    </Badge>
                    <button className="icon-button" type="button" title="Ιστορικό εκδόσεων" onClick={() => void action(async () => {
                      const history = await apiFetch<ProposalVersionResponse[]>(`/v1/proposals/sections/${selected.id}/versions`);
                      setVersions(history);
                      setShowVersions((current) => !current);
                    })}><FileClock size={16} /></button>
                    <button className="button button-primary" type="button" disabled={busy || body.trim() === selected.body.trim()} onClick={() => void action(() => updateSection({ body, change_summary: "Edited in proposal workspace" }))}><Save size={15} />Νέα έκδοση</button>
                  </div>
                  <textarea value={body} onChange={(event) => setBody(event.target.value)} aria-label={`Απάντηση: ${selected.title}`} />
                  <div className="proposal-evidence">
                    <strong><BookOpenCheck size={15} />Επίσημη τεκμηρίωση</strong>
                    {selected.citations.map((citation, index) => {
                      const url = citationValue(citation, "source_url");
                      return (
                        <div key={`${citationValue(citation, "document_id") ?? "citation"}-${index}`}>
                          <span>[{index + 1}] {citationValue(citation, "document_title") ?? "Επίσημο έγγραφο"}{citationValue(citation, "page") ? ` · σελ. ${citationValue(citation, "page")}` : ""}</span>
                          {url ? <a href={url} target="_blank" rel="noreferrer" title="Άνοιγμα πηγής"><ExternalLink size={13} /></a> : null}
                          {citationValue(citation, "excerpt") ? <small>{citationValue(citation, "excerpt")}</small> : null}
                        </div>
                      );
                    })}
                    {!selected.citations.length ? <small>Δεν έχει συνδεθεί επίσημο απόσπασμα. Οι μη τεκμηριωμένοι ισχυρισμοί πρέπει να παραμείνουν [TODO].</small> : null}
                  </div>
                  {showVersions ? (
                    <div className="proposal-version-list">
                      {versions.map((version) => (
                        <button type="button" key={version.id} onClick={() => setBody(version.body)}>
                          <strong>v{version.version_number}</strong>
                          <span>{version.change_summary ?? "Version snapshot"}</span>
                          <small>{new Intl.DateTimeFormat("el-GR", { dateStyle: "short", timeStyle: "short" }).format(new Date(version.created_at))}</small>
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : (
            <EmptyState title="Δεν έχουν δημιουργηθεί απαντήσεις" detail="Καταγράψτε ή εξαγάγετε απαιτήσεις και δημιουργήστε ένα evidence-grounded πρώτο draft." />
          )}
        </>
      ) : (
        <div className="proposal-library-layout">
          <form onSubmit={(event) => {
            event.preventDefault();
            if (!contentTitle.trim() || !contentBody.trim()) return;
            void action(async () => {
              await apiFetch<BidContentResponse>("/v1/proposals/library", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  title: contentTitle.trim(),
                  content_type: "RESPONSE",
                  body: contentBody.trim(),
                  tags: contentTags.split(",").map((tag) => tag.trim()).filter(Boolean),
                  approved: true,
                }),
              });
              await reload();
              setContentTitle("");
              setContentBody("");
              setContentTags("");
            });
          }}>
            <label>Τίτλος<input value={contentTitle} onChange={(event) => setContentTitle(event.target.value)} /></label>
            <label>Εγκεκριμένο περιεχόμενο<textarea value={contentBody} onChange={(event) => setContentBody(event.target.value)} rows={7} /></label>
            <label>Tags<input value={contentTags} onChange={(event) => setContentTags(event.target.value)} placeholder="GIS, ISO, μεθοδολογία" /></label>
            <button className="button button-primary" type="submit" disabled={busy || !contentTitle.trim() || !contentBody.trim()}><Plus size={15} />Προσθήκη</button>
          </form>
          <div className="proposal-library-list">
            {library.map((item) => (
              <article key={item.id}>
                <header><strong>{item.title}</strong><Badge tone={item.approved ? "green" : "amber"}>{item.approved ? <><Check size={12} />approved</> : "draft"}</Badge></header>
                <p>{item.body}</p>
                <footer><span>{item.tags.join(" · ") || item.content_type}</span><button className="icon-button is-danger" type="button" title="Διαγραφή" onClick={() => void action(async () => { await apiFetch(`/v1/proposals/library/${item.id}`, { method: "DELETE" }); await reload(); })}><Trash2 size={14} /></button></footer>
              </article>
            ))}
            {!library.length ? <EmptyState title="Η βιβλιοθήκη απαντήσεων είναι κενή" /> : null}
          </div>
        </div>
      )}
    </section>
  );
}

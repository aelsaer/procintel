"use client";

import { useEffect, useState } from "react";
import { ExternalLink, FileDiff, ListChecks, Loader2, MessageSquareText, Send, Sparkles } from "lucide-react";
import { apiFetch, type DocumentAnswer } from "@/lib/api";
import { Badge } from "@/components/procurement-ui";

interface Exchange {
  question: string;
  response: DocumentAnswer;
}

interface ComplianceField {
  id: string;
  document_id: string;
  page_number: number | null;
  category: string;
  field_name: string;
  value: { raw?: string; normalized?: unknown; value_type?: string };
  source_excerpt: string | null;
  confidence: number | string;
}

interface DocumentComparison {
  id: string;
  summary: string;
  changes: {
    counts?: Record<string, number>;
    items?: Array<{
      change_type: string;
      base: { page: number; text: string } | null;
      comparison: { page: number; text: string } | null;
    }>;
  };
  created_at: string;
}

export function DocumentIntelligence({ processId }: { processId: string }) {
  const [question, setQuestion] = useState("");
  const [history, setHistory] = useState<Exchange[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fields, setFields] = useState<ComplianceField[]>([]);
  const [comparisons, setComparisons] = useState<DocumentComparison[]>([]);

  useEffect(() => {
    let active = true;
    void Promise.all([
      apiFetch<ComplianceField[]>(`/v1/document-intelligence/${processId}/compliance`),
      apiFetch<DocumentComparison[]>(`/v1/document-intelligence/${processId}/comparisons`),
    ]).then(([nextFields, nextComparisons]) => {
      if (!active) return;
      setFields(nextFields);
      setComparisons(nextComparisons);
    }).catch(() => undefined);
    return () => { active = false; };
  }, [processId]);

  async function runAction(action: "extract-compliance" | "compare") {
    setBusy(true);
    setError(null);
    try {
      await apiFetch(`/v1/document-intelligence/${processId}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: action === "compare" ? JSON.stringify({}) : undefined,
      });
      const [nextFields, nextComparisons] = await Promise.all([
        apiFetch<ComplianceField[]>(`/v1/document-intelligence/${processId}/compliance`),
        apiFetch<DocumentComparison[]>(`/v1/document-intelligence/${processId}/comparisons`),
      ]);
      setFields(nextFields);
      setComparisons(nextComparisons);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Η ανάλυση εγγράφων απέτυχε.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="document-intelligence-stack">
      <section className="document-structured" aria-labelledby="document-structured-title">
        <div className="document-assistant-heading">
          <div><span>Structured compliance</span><h3 id="document-structured-title">Όροι, απαιτήσεις και μεταβολές</h3></div>
          <div className="document-structured-actions">
            <button className="button button-secondary" type="button" disabled={busy} onClick={() => void runAction("extract-compliance")}><Sparkles size={15} />Εξαγωγή πεδίων</button>
            <button className="button button-secondary" type="button" disabled={busy} onClick={() => void runAction("compare")}><FileDiff size={15} />Σύγκριση εκδόσεων</button>
          </div>
        </div>
        <div className="document-structured-grid">
          <section>
            <div className="coverage-section-heading"><h4><ListChecks size={15} /> Compliance fields</h4><Badge tone="blue">{fields.length}</Badge></div>
            <div className="document-field-list">
              {fields.slice(0, 40).map((field) => (
                <article key={field.id}>
                  <span><Badge>{field.category}</Badge><strong>{field.field_name.replaceAll("_", " ")}</strong></span>
                  <p>{String(field.value.normalized ?? field.value.raw ?? "")}</p>
                  <small>σελ. {field.page_number ?? "—"} · {Math.round(Number(field.confidence) * 100)}% confidence</small>
                  {field.source_excerpt && <q>{field.source_excerpt}</q>}
                </article>
              ))}
              {!fields.length && <p className="document-structured-empty">Δεν έχουν εξαχθεί δομημένα πεδία.</p>}
            </div>
          </section>
          <section>
            <div className="coverage-section-heading"><h4><FileDiff size={15} /> Document changes</h4><Badge tone="amber">{comparisons.length}</Badge></div>
            <div className="document-comparison-list">
              {comparisons.map((comparison) => (
                <article key={comparison.id}>
                  <strong>{comparison.summary}</strong>
                  <div className="badge-row">
                    {Object.entries(comparison.changes.counts ?? {}).map(([kind, count]) => <Badge key={kind} tone={kind === "ADDED" ? "green" : kind === "REMOVED" ? "red" : "amber"}>{kind} {count}</Badge>)}
                  </div>
                  {(comparison.changes.items ?? []).slice(0, 6).map((change, index) => (
                    <div className="document-change" key={`${change.change_type}-${index}`}>
                      <Badge>{change.change_type}</Badge>
                      <span>{change.comparison?.text ?? change.base?.text}</span>
                    </div>
                  ))}
                </article>
              ))}
              {!comparisons.length && <p className="document-structured-empty">Χρειάζονται δύο επεξεργασμένες εκδόσεις για σύγκριση.</p>}
            </div>
          </section>
        </div>
      </section>
      <section className="document-assistant" aria-labelledby="document-assistant-title">
      <div className="document-assistant-heading">
        <div>
          <span>Document intelligence</span>
          <h3 id="document-assistant-title">Ερωτήσεις στα τεύχη</h3>
        </div>
        <Badge tone="blue">Evidence cited</Badge>
      </div>

      <div className="document-chat-log" aria-live="polite">
        {history.length === 0 && (
          <div className="document-chat-empty">
            <MessageSquareText size={24} aria-hidden="true" />
            <p>Ρωτήστε για προϋποθέσεις, πιστοποιητικά, προθεσμίες, παραδοτέα ή όρους συμμετοχής.</p>
          </div>
        )}
        {history.map((exchange, exchangeIndex) => (
          <article className="document-exchange" key={`${exchange.question}-${exchangeIndex}`}>
            <p className="document-question">{exchange.question}</p>
            <div className="document-answer">
              <div className="document-answer-meta">
                <Badge tone={exchange.response.mode === "LLM_GROUNDED" ? "green" : "blue"}>
                  {exchange.response.mode.replace("_", " ")}
                </Badge>
              </div>
              <p>{exchange.response.answer}</p>
              {exchange.response.citations.length > 0 && (
                <ol className="document-citations">
                  {exchange.response.citations.map((citation, index) => (
                    <li key={`${citation.document_id}-${citation.page}-${index}`}>
                      {citation.source_url ? (
                        <a href={citation.source_url} target="_blank" rel="noreferrer">
                          <span>[{index + 1}] {citation.document_title ?? "Επίσημο έγγραφο"} · σελ. {citation.page}</span>
                          <ExternalLink size={13} aria-hidden="true" />
                        </a>
                      ) : (
                        <span>[{index + 1}] {citation.document_title ?? "Έγγραφο"} · σελ. {citation.page}</span>
                      )}
                      <small>{citation.excerpt}</small>
                    </li>
                  ))}
                </ol>
              )}
              <small className="document-limitations">{exchange.response.limitations}</small>
            </div>
          </article>
        ))}
        {busy && <div className="document-thinking"><Loader2 className="spin" size={16} /> Αναζήτηση στα επεξεργασμένα αρχεία</div>}
      </div>

      <form
        className="document-chat-form"
        onSubmit={(event) => {
          event.preventDefault();
          const value = question.trim();
          if (value.length < 3) return;
          setBusy(true);
          setError(null);
          void apiFetch<DocumentAnswer>("/v1/document-intelligence/ask", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ process_id: processId, question: value }),
          })
            .then((response) => {
              setHistory((current) => [...current, { question: value, response }]);
              setQuestion("");
            })
            .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Η ερώτηση απέτυχε."))
            .finally(() => setBusy(false));
        }}
      >
        <label className="sr-only" htmlFor="document-question">Ερώτηση στα έγγραφα</label>
        <input
          id="document-question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="π.χ. Ποια πιστοποιητικά είναι υποχρεωτικά;"
          disabled={busy}
        />
        <button className="icon-button" type="submit" title="Υποβολή ερώτησης" disabled={busy || question.trim().length < 3}>
          <Send size={17} />
        </button>
      </form>
      {error && <p className="form-error" role="alert">{error}</p>}
      </section>
    </div>
  );
}

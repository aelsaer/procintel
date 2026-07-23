"use client";

import { useEffect, useState } from "react";
import { ArrowLeftRight, Check, RefreshCw, RotateCcw, ShieldAlert, X } from "lucide-react";
import { api, type EntityMatchCandidateResponse, type EntityMergeHistoryResponse } from "@/lib/api";
import { Badge, EmptyState, ErrorState, LoadingState } from "@/components/procurement-ui";

export function EntityReviewWorkspace() {
  const [candidates, setCandidates] = useState<EntityMatchCandidateResponse[]>([]);
  const [merges, setMerges] = useState<EntityMergeHistoryResponse[]>([]);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  async function refresh() {
    try {
      const [nextCandidates, nextMerges] = await Promise.all([api.getEntityCandidates(), api.getEntityMerges()]);
      setCandidates(nextCandidates); setMerges(nextMerges); setError(null);
    } catch (nextError) { setError(nextError); }
    finally { setLoading(false); }
  }

  useEffect(() => { let active = true; void Promise.all([api.getEntityCandidates(), api.getEntityMerges()]).then(([nextCandidates, nextMerges]) => { if (active) { setCandidates(nextCandidates); setMerges(nextMerges); setLoading(false); } }).catch((nextError) => { if (active) { setError(nextError); setLoading(false); } }); return () => { active = false; }; }, []);

  async function generate() {
    setBusy(true);
    try { await api.generateEntityCandidates(); await refresh(); }
    catch (nextError) { setError(nextError); }
    finally { setBusy(false); }
  }

  async function review(candidate: EntityMatchCandidateResponse, action: string) {
    setBusy(true);
    try { await api.reviewEntityCandidate(candidate.id, action, notes); setNotes(""); await refresh(); }
    catch (nextError) { setError(nextError); }
    finally { setBusy(false); }
  }

  async function undo(merge: EntityMergeHistoryResponse) {
    setBusy(true);
    try { await api.undoEntityMerge(merge.id); await refresh(); }
    catch (nextError) { setError(nextError); }
    finally { setBusy(false); }
  }

  return (
    <div className="entity-review-workspace">
      <div className="review-toolbar"><div><span className="eyebrow">Data stewardship</span><h2>Entity resolution review</h2></div><button className="button button-secondary" type="button" onClick={() => void generate()} disabled={busy}><RefreshCw className={busy ? "spin" : ""} size={15} />Παραγωγή υποψηφίων</button></div>
      {Boolean(error) && <ErrorState title="Δεν φορτώθηκε η ουρά entity review" error={error} />}
      {loading && <LoadingState label="Φόρτωση υποψηφίων ταύτισης" />}
      <label className="review-notes"><span>Σημείωση απόφασης</span><input value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="Τεκμηρίωση merge ή rejection" /></label>
      <div className="entity-candidate-list">
        {candidates.map((candidate) => <article key={candidate.id}><div className="candidate-score"><strong>{Math.round(candidate.score * 100)}</strong><small>confidence</small></div><div className="candidate-entities"><span><strong>{candidate.entity_a.name}</strong><small>{candidate.entity_a.entity_type}</small></span><ArrowLeftRight size={17} /><span><strong>{candidate.entity_b.name}</strong><small>{candidate.entity_b.entity_type}</small></span><p>{candidate.blocking_reason} · name {String(candidate.score_breakdown.name_similarity ?? "-")}</p></div><div className="candidate-actions"><button className="icon-button" type="button" onClick={() => void review(candidate, "MERGE_A_INTO_B")} disabled={busy} aria-label="Συγχώνευση πρώτης στη δεύτερη"><Check size={15} /></button><button className="icon-button" type="button" onClick={() => void review(candidate, "MERGE_B_INTO_A")} disabled={busy} aria-label="Συγχώνευση δεύτερης στην πρώτη"><ArrowLeftRight size={15} /></button><button className="icon-button" type="button" onClick={() => void review(candidate, "REJECT")} disabled={busy} aria-label="Απόρριψη ταύτισης"><X size={15} /></button></div></article>)}
        {!candidates.length && !loading && <EmptyState title="Η ουρά review είναι καθαρή" detail="Η παραγωγή υποψηφίων χρησιμοποιεί fuzzy name, διεύθυνση και ταχυδρομικό κώδικα, με hard block σε συγκρουόμενα ΑΦΜ." />}
      </div>
      <section className="merge-history"><div className="panel-heading"><div><span className="eyebrow">Reversible decisions</span><h3>Ιστορικό συγχωνεύσεων</h3></div><ShieldAlert size={17} /></div><div className="compact-list">{merges.slice(0, 10).map((merge) => <div className="compact-row" key={merge.id}><ArrowLeftRight size={15} /><span>{merge.merged_entity_id}<small>στο {merge.surviving_entity_id} · {merge.performed_by}</small></span>{merge.reverted_at ? <Badge>REVERTED</Badge> : <button className="icon-button" type="button" onClick={() => void undo(merge)} aria-label="Αναίρεση συγχώνευσης"><RotateCcw size={14} /></button>}</div>)}{!merges.length && <EmptyState title="Δεν υπάρχουν συγχωνεύσεις" />}</div></section>
    </div>
  );
}

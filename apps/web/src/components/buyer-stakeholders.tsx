"use client";

import { useState } from "react";
import {
  BellOff,
  BellPlus,
  ExternalLink,
  Mail,
  Phone,
  ShieldCheck,
  UserRoundSearch,
} from "lucide-react";

import { Badge, EmptyState, ErrorState, LoadingState, Section } from "@/components/procurement-ui";
import { api, type DecisionMakerListResponse } from "@/lib/api";

const roleLabels: Record<string, string> = {
  PROCUREMENT: "Προμήθειες",
  FINANCE: "Οικονομικά",
  TECHNICAL: "Τεχνικός ρόλος",
  DEPARTMENT_HEAD: "Διεύθυνση",
  SIGNATORY: "Υπογράφων",
  STAKEHOLDER: "Εμπλεκόμενος",
};

type Props = {
  response: DecisionMakerListResponse | null;
  loading: boolean;
  error: unknown;
  onRefresh: () => Promise<unknown>;
};

export function BuyerStakeholders({ response, loading, error, onRefresh }: Props) {
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  async function toggleWatch(id: string, watched: boolean) {
    setPendingId(id);
    setActionError(null);
    try {
      if (watched) await api.unwatchDecisionMaker(id);
      else await api.watchDecisionMaker(id);
      await onRefresh();
    } catch (requestError) {
      setActionError(requestError instanceof Error ? requestError.message : "Η ενέργεια απέτυχε.");
    } finally {
      setPendingId(null);
    }
  }

  return (
    <Section title="Stakeholder intelligence" eyebrow="Official public records">
      {loading ? <LoadingState label="Φόρτωση στελεχών φορέα" /> : null}
      {error ? <ErrorState error={error} /> : null}
      {actionError ? <p className="form-error" role="alert">{actionError}</p> : null}
      {response?.stakeholders.length ? (
        <div className="stakeholder-grid">
          {response.stakeholders.map((person) => (
            <article className="stakeholder-card" key={person.id}>
              <header>
                <span className="stakeholder-avatar" aria-hidden="true">
                  <UserRoundSearch size={19} />
                </span>
                <span>
                  <strong>{person.full_name}</strong>
                  <small>{person.job_title ?? person.department ?? "Δημοσιευμένος εμπλεκόμενος"}</small>
                </span>
                <button
                  className={`icon-button ${person.watched ? "is-active" : ""}`}
                  type="button"
                  onClick={() => void toggleWatch(person.id, person.watched)}
                  disabled={pendingId === person.id}
                  title={person.watched ? "Αφαίρεση από watchlist" : "Παρακολούθηση στελέχους"}
                  aria-label={person.watched ? `Αφαίρεση ${person.full_name} από watchlist` : `Παρακολούθηση ${person.full_name}`}
                >
                  {person.watched ? <BellOff size={16} /> : <BellPlus size={16} />}
                </button>
              </header>

              <div className="badge-row">
                <Badge tone="blue">{roleLabels[person.decision_role] ?? person.decision_role}</Badge>
                <Badge tone={person.is_current ? "green" : "neutral"}>
                  {person.is_current ? "Ενεργός" : "Ιστορικός"}
                </Badge>
                <Badge>{Math.round(person.confidence * 100)}% confidence</Badge>
              </div>

              {(person.email || person.phone) ? (
                <div className="stakeholder-contact">
                  {person.email ? <a href={`mailto:${person.email}`}><Mail size={14} />{person.email}</a> : null}
                  {person.phone ? <a href={`tel:${person.phone}`}><Phone size={14} />{person.phone}</a> : null}
                </div>
              ) : null}

              {person.recent_involvement.length ? (
                <div className="stakeholder-activity">
                  <small>Πρόσφατη επίσημη εμπλοκή</small>
                  {person.recent_involvement.slice(0, 3).map((item, index) => (
                    item.official_url ? (
                      <a
                        href={item.official_url}
                        target="_blank"
                        rel="noreferrer"
                        key={`${item.official_identifier ?? "decision"}-${item.event_date ?? index}`}
                      >
                        <span>{item.official_identifier ?? "Απόφαση Διαύγειας"}</span>
                        <ExternalLink size={13} />
                      </a>
                    ) : null
                  ))}
                </div>
              ) : null}

              <footer>
                <ShieldCheck size={14} aria-hidden="true" />
                <span>{person.source_system} · δημόσιο επίσημο αρχείο</span>
                {person.source_url ? (
                  <a href={person.source_url} target="_blank" rel="noreferrer" title="Πηγή">
                    <ExternalLink size={13} />
                  </a>
                ) : null}
              </footer>
            </article>
          ))}
        </div>
      ) : null}
      {!loading && response && !response.stakeholders.length ? (
        <EmptyState title="Δεν έχουν συνδεθεί δημοσιευμένα στελέχη με αυτόν τον φορέα" />
      ) : null}
      {response ? <p className="section-methodology">{response.methodology}</p> : null}
    </Section>
  );
}

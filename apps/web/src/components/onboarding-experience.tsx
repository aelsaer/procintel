"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  ArrowRight,
  Building2,
  Check,
  CheckCircle2,
  Loader2,
  MapPin,
  SearchCheck,
  ShieldCheck,
  Sparkles,
  UserRoundCheck,
} from "lucide-react";
import {
  api,
  type InitialOpportunityResponse,
  type OnboardingCompleteResponse,
  type OnboardingStatusResponse,
  type ProfileTermResponse,
} from "@/lib/api";
import { Badge, getErrorMessage } from "@/components/procurement-ui";

type Step = "company" | "scope" | "results";

const REGIONS = [
  ["", "Όλη η Ελλάδα"],
  ["EL30", "Αττική"],
  ["EL41", "Βόρειο Αιγαίο"],
  ["EL42", "Νότιο Αιγαίο"],
  ["EL43", "Κρήτη"],
  ["EL51", "Ανατολική Μακεδονία και Θράκη"],
  ["EL52", "Κεντρική Μακεδονία"],
  ["EL53", "Δυτική Μακεδονία"],
  ["EL54", "Ήπειρος"],
  ["EL61", "Θεσσαλία"],
  ["EL62", "Ιόνια Νησιά"],
  ["EL63", "Δυτική Ελλάδα"],
  ["EL64", "Στερεά Ελλάδα"],
  ["EL65", "Πελοπόννησος"],
] as const;

function currency(value: number | string | null) {
  if (value === null) return "Μη δηλωμένη αξία";
  return new Intl.NumberFormat("el-GR", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 0,
  }).format(Number(value));
}

function OpportunityResult({ item }: { item: InitialOpportunityResponse }) {
  return (
    <article className="onboarding-opportunity">
      <div className="onboarding-opportunity-score">
        <strong>{Math.round(item.score)}</strong>
        <span>fit</span>
      </div>
      <div>
        <div className="onboarding-opportunity-meta">
          {item.adam && <span>{item.adam}</span>}
          <span>{currency(item.amount)}</span>
          {item.locations[0] && <span><MapPin size={12} />{item.locations[0]}</span>}
        </div>
        <h3>{item.title}</h3>
        <p>{item.buyer_name ?? "Μη ταυτοποιημένος φορέας"}</p>
      </div>
      <Link
        className="icon-button"
        href={`/processes/${item.process_id}`}
        aria-label={`Άνοιγμα ${item.title}`}
      >
        <ArrowRight size={17} />
      </Link>
    </article>
  );
}

export function OnboardingExperience({ onFinished }: { onFinished: () => void }) {
  const [status, setStatus] = useState<OnboardingStatusResponse | null>(null);
  const [step, setStep] = useState<Step>("company");
  const [companyName, setCompanyName] = useState("");
  const [description, setDescription] = useState("");
  const [suggestions, setSuggestions] = useState<ProfileTermResponse[]>([]);
  const [keywordSuggestions, setKeywordSuggestions] = useState<ProfileTermResponse[]>([]);
  const [selectedCpvs, setSelectedCpvs] = useState<string[]>([]);
  const [selectedKeywords, setSelectedKeywords] = useState<string[]>([]);
  const [region, setRegion] = useState("");
  const [manualCpv, setManualCpv] = useState("");
  const [humanReview, setHumanReview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<OnboardingCompleteResponse | null>(null);

  useEffect(() => {
    let active = true;
    void api.getOnboardingStatus()
      .then((next) => {
        if (!active) return;
        setStatus(next);
        setDescription(next.description);
        setSelectedCpvs(next.selected_cpv_codes);
        setSelectedKeywords(next.selected_keywords);
        setRegion(next.selected_nuts_codes[0] ?? "");
        if (next.current_step === "CONFIRM_SCOPE") setStep("scope");
      })
      .catch(() => {
        // The workspace remains usable if onboarding is unavailable during a
        // rolling deployment; operational status will expose that incident.
      });
    return () => { active = false; };
  }, []);

  const cpvSuggestionMap = useMemo(
    () => new Map(suggestions.map((suggestion) => [suggestion.value, suggestion])),
    [suggestions],
  );

  if (!status?.required && !result) return null;

  function toggle(values: string[], value: string, setter: (next: string[]) => void) {
    setter(values.includes(value) ? values.filter((item) => item !== value) : [...values, value]);
  }

  async function inferScope(event: FormEvent) {
    event.preventDefault();
    if (description.trim().length < 12 || companyName.trim().length < 2) return;
    setBusy(true);
    setError(null);
    try {
      const response = await api.suggestOnboardingProfile(description);
      setSuggestions(response.cpv_suggestions);
      setKeywordSuggestions(response.keyword_suggestions);
      setSelectedCpvs(
        response.cpv_suggestions
          .filter((item) => item.confidence >= 0.72)
          .slice(0, 8)
          .map((item) => item.value),
      );
      setSelectedKeywords(response.keyword_suggestions.slice(0, 8).map((item) => item.value));
      setStep("scope");
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  function addManualCpv(event: FormEvent) {
    event.preventDefault();
    const normalized = manualCpv.split("-", 1)[0].replace(/\D/g, "");
    if (normalized.length < 2 || normalized.length > 8) {
      setError("Ο CPV πρέπει να περιέχει 2 έως 8 ψηφία.");
      return;
    }
    if (!selectedCpvs.includes(normalized)) setSelectedCpvs([...selectedCpvs, normalized]);
    setManualCpv("");
    setError(null);
  }

  async function complete() {
    if (!selectedCpvs.length) {
      setError("Επιβεβαιώστε τουλάχιστον έναν CPV.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const response = await api.completeOnboarding({
        company_name: companyName,
        company_description: description,
        selected_cpv_codes: selectedCpvs,
        selected_keywords: selectedKeywords,
        selected_nuts_codes: region ? [region] : [],
        request_human_review: humanReview,
      });
      setResult(response);
      setStep("results");
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="onboarding-backdrop">
      <section className="onboarding-dialog" role="dialog" aria-modal="true" aria-labelledby="onboarding-title">
        <aside className="onboarding-rail">
          <div className="onboarding-brand"><span><Building2 size={18} /></span>Procintel</div>
          <ol>
            <li className={step === "company" ? "is-active" : "is-complete"}>
              <span>{step !== "company" ? <Check size={15} /> : "1"}</span>
              <div><strong>Επιχείρηση</strong><small>Αντικείμενο και ειδίκευση</small></div>
            </li>
            <li className={step === "scope" ? "is-active" : step === "results" ? "is-complete" : ""}>
              <span>{step === "results" ? <Check size={15} /> : "2"}</span>
              <div><strong>Αγορά</strong><small>CPV, όροι και περιοχή</small></div>
            </li>
            <li className={step === "results" ? "is-active" : ""}>
              <span>3</span>
              <div><strong>Ευκαιρίες</strong><small>Πρώτη shortlist</small></div>
            </li>
          </ol>
          <div className="onboarding-assurance">
            <ShieldCheck size={18} />
            <span><strong>Ελεγχόμενο προφίλ</strong><small>Οι CPV ενεργοποιούνται μόνο μετά από επιβεβαίωση.</small></span>
          </div>
        </aside>

        <div className="onboarding-main">
          {step === "company" && (
            <form className="onboarding-step" onSubmit={inferScope}>
              <header>
                <span className="eyebrow">Νέο workspace</span>
                <h1 id="onboarding-title">Τι αναλαμβάνει η εταιρεία;</h1>
                <p>Χρησιμοποιήστε πραγματικά προϊόντα, υπηρεσίες και τύπους έργων.</p>
              </header>
              <label>
                <span>Επωνυμία</span>
                <input
                  value={companyName}
                  onChange={(event) => setCompanyName(event.target.value)}
                  placeholder="Επωνυμία επιχείρησης"
                  autoFocus
                  required
                  minLength={2}
                />
              </label>
              <label className="onboarding-description">
                <span>Αντικείμενο</span>
                <textarea
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  placeholder="Παρέχουμε υπηρεσίες γεωπληροφορικής, ανάπτυξη GIS εφαρμογών και χαρτογραφικές μελέτες για δημόσιους φορείς."
                  required
                  minLength={12}
                />
                <small>{description.trim().split(/\s+/).filter(Boolean).length} λέξεις</small>
              </label>
              {error && <div className="onboarding-error" role="alert">{error}</div>}
              <footer>
                <span />
                <button className="button button-primary" type="submit" disabled={busy || description.trim().length < 12 || companyName.trim().length < 2}>
                  {busy ? <Loader2 className="spin" size={16} /> : <Sparkles size={16} />}
                  Εύρεση αγοράς
                </button>
              </footer>
            </form>
          )}

          {step === "scope" && (
            <div className="onboarding-step onboarding-scope">
              <header>
                <span className="eyebrow">Επιβεβαίωση αγοράς</span>
                <h1 id="onboarding-title">Επιλέξτε τι θα παρακολουθείται</h1>
                <p>Οι επιλογές αποθηκεύονται ως το κοινό προφίλ του workspace.</p>
              </header>
              <section>
                <div className="onboarding-section-heading">
                  <div><strong>Προτεινόμενοι CPV</strong><small>{selectedCpvs.length} επιλεγμένοι</small></div>
                  <SearchCheck size={18} />
                </div>
                <div className="onboarding-token-grid">
                  {suggestions.map((suggestion) => (
                    <button
                      key={suggestion.value}
                      type="button"
                      className={selectedCpvs.includes(suggestion.value) ? "is-selected" : ""}
                      onClick={() => toggle(selectedCpvs, suggestion.value, setSelectedCpvs)}
                      title={suggestion.reason}
                    >
                      <span>{selectedCpvs.includes(suggestion.value) && <Check size={13} />}{suggestion.value}</span>
                      <strong>{suggestion.label}</strong>
                      <small>{Math.round(suggestion.confidence * 100)}% confidence</small>
                    </button>
                  ))}
                </div>
                <form className="onboarding-manual-cpv" onSubmit={addManualCpv}>
                  <input value={manualCpv} onChange={(event) => setManualCpv(event.target.value)} placeholder="Προσθήκη CPV" aria-label="Χειροκίνητη προσθήκη CPV" />
                  <button className="button button-secondary" type="submit">Προσθήκη</button>
                </form>
                {selectedCpvs.filter((code) => !cpvSuggestionMap.has(code)).length > 0 && (
                  <div className="onboarding-selected-manual">
                    {selectedCpvs.filter((code) => !cpvSuggestionMap.has(code)).map((code) => (
                      <button type="button" key={code} onClick={() => toggle(selectedCpvs, code, setSelectedCpvs)}>CPV {code} <span>×</span></button>
                    ))}
                  </div>
                )}
              </section>
              <section className="onboarding-scope-row">
                <label>
                  <span>Περιοχή δραστηριοποίησης</span>
                  <select value={region} onChange={(event) => setRegion(event.target.value)}>
                    {REGIONS.map(([code, label]) => <option key={code || "all"} value={code}>{label}</option>)}
                  </select>
                </label>
                <div>
                  <span>Ειδικοί όροι</span>
                  <div className="onboarding-keywords">
                    {keywordSuggestions.slice(0, 10).map((keyword) => (
                      <button
                        type="button"
                        key={keyword.value}
                        className={selectedKeywords.includes(keyword.value) ? "is-selected" : ""}
                        onClick={() => toggle(selectedKeywords, keyword.value, setSelectedKeywords)}
                      >
                        {keyword.value}
                      </button>
                    ))}
                  </div>
                </div>
              </section>
              <label className="onboarding-review-choice">
                <input type="checkbox" checked={humanReview} onChange={(event) => setHumanReview(event.target.checked)} />
                <UserRoundCheck size={19} />
                <span><strong>Έλεγχος από σύμβουλο</strong><small>Το προφίλ μπαίνει στην ουρά ποιοτικού ελέγχου.</small></span>
              </label>
              {error && <div className="onboarding-error" role="alert">{error}</div>}
              <footer>
                <button className="button button-ghost" type="button" onClick={() => setStep("company")}><ArrowLeft size={16} />Πίσω</button>
                <button className="button button-primary" type="button" onClick={() => void complete()} disabled={busy || !selectedCpvs.length}>
                  {busy ? <Loader2 className="spin" size={16} /> : <SearchCheck size={16} />}
                  Δημιουργία shortlist
                </button>
              </footer>
            </div>
          )}

          {step === "results" && result && (
            <div className="onboarding-step onboarding-results">
              <header>
                <div className="onboarding-success-mark"><CheckCircle2 size={24} /></div>
                <span className="eyebrow">Το workspace είναι έτοιμο</span>
                <h1 id="onboarding-title">{result.opportunities.length} ευκαιρίες για πρώτη αξιολόγηση</h1>
                <p>Το καθημερινό digest δημιουργήθηκε με το επιβεβαιωμένο προφίλ.</p>
                <div className="onboarding-quality">
                  <Badge tone={result.quality_score >= 80 ? "green" : "amber"}>{Math.round(result.quality_score)}% ποιότητα προφίλ</Badge>
                  {result.review_status && <Badge tone="blue">Human review {result.review_status.toLocaleLowerCase()}</Badge>}
                </div>
              </header>
              <div className="onboarding-opportunities">
                {result.opportunities.map((opportunity) => <OpportunityResult key={opportunity.process_id} item={opportunity} />)}
              </div>
              {!result.opportunities.length && (
                <div className="onboarding-no-results">
                  <SearchCheck size={22} />
                  <strong>Δεν βρέθηκε αυστηρή αντιστοίχιση στο διαθέσιμο αρχείο</strong>
                  <span>Το προφίλ αποθηκεύτηκε και ο ποιοτικός έλεγχος μπορεί να διευρύνει τους όρους.</span>
                </div>
              )}
              <footer>
                <span />
                <button className="button button-primary" type="button" onClick={onFinished}>
                  Άνοιγμα Ευκαιριών <ArrowRight size={16} />
                </button>
              </footer>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

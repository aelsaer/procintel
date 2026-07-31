"use client";

import { FormEvent, useEffect, useState } from "react";
import { Building2, Check, Loader2, Search, X } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Badge, EmptyState } from "@/components/procurement-ui";

type RegistryCompany = {
  afm_normalized: string;
  gemi_number: string | null;
  official_name: string | null;
  trade_name: string | null;
  company_status: string | null;
  legal_form: string | null;
  kad_codes: string[];
  municipality: string | null;
};

type FundingReview = {
  id: string;
  confidence: number | string;
  link_method: string;
  evidence: Record<string, unknown>;
  process_id: string | null;
  act_title: string | null;
  mis_ops_code: string | null;
  funding_title: string;
};

export function SourceOperations() {
  const [name, setName] = useState("");
  const [kad, setKad] = useState("");
  const [registry, setRegistry] = useState<RegistryCompany[]>([]);
  const [funding, setFunding] = useState<FundingReview[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadFunding() {
    try {
      setFunding(await apiFetch<FundingReview[]>("/v1/intelligence/funding-links/review"));
    } catch {
      setFunding([]);
    }
  }

  useEffect(() => {
    let active = true;
    void apiFetch<FundingReview[]>("/v1/intelligence/funding-links/review")
      .then((rows) => {
        if (active) setFunding(rows);
      })
      .catch(() => {
        if (active) setFunding([]);
      });
    return () => {
      active = false;
    };
  }, []);

  async function searchRegistry(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() && !kad.trim()) return;
    setBusy(true);
    setError(null);
    const query = new URLSearchParams();
    if (name.trim()) query.set("name", name.trim());
    if (kad.trim()) query.set("kad", kad.trim());
    try {
      setRegistry(await apiFetch<RegistryCompany[]>(`/v1/companies/registry/search?${query}`));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Η αναζήτηση ΓΕΜΗ απέτυχε.");
    } finally {
      setBusy(false);
    }
  }

  async function review(linkId: string, status: "ACCEPTED" | "REJECTED") {
    setBusy(true);
    setError(null);
    try {
      await apiFetch(`/v1/intelligence/funding-links/${linkId}/review`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      await loadFunding();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Η αξιολόγηση σύνδεσης απέτυχε.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="source-operations">
      <section aria-labelledby="gemi-search-title">
        <div className="coverage-section-heading"><h3 id="gemi-search-title">Αναζήτηση μητρώου ΓΕΜΗ</h3><Badge tone="blue">Live OpenData</Badge></div>
        <form className="source-search-form" onSubmit={searchRegistry}>
          <label><span>Επωνυμία</span><input value={name} onChange={(event) => setName(event.target.value)} placeholder="Επωνυμία ή διακριτικός τίτλος" /></label>
          <label><span>ΚΑΔ</span><input value={kad} onChange={(event) => setKad(event.target.value)} placeholder="π.χ. 6201" /></label>
          <button className="button button-secondary" type="submit" disabled={busy || (!name.trim() && !kad.trim())}>{busy ? <Loader2 className="spin" size={15} /> : <Search size={15} />}Αναζήτηση</button>
        </form>
        <div className="source-operation-list">
          {registry.map((company) => <article key={`${company.gemi_number}-${company.afm_normalized}`}>
            <Building2 size={17} />
            <span><strong>{company.official_name ?? company.trade_name ?? company.afm_normalized}</strong><small>ΑΦΜ {company.afm_normalized} · ΓΕΜΗ {company.gemi_number ?? "—"} · {company.municipality ?? "χωρίς δήμο"}</small></span>
            <span className="badge-row">{company.company_status && <Badge tone="green">{company.company_status}</Badge>}{company.kad_codes.slice(0, 3).map((code) => <Badge key={code}>{code}</Badge>)}</span>
            {company.gemi_number ? <button className="button button-secondary" type="button" onClick={() => {
              setBusy(true);
              void apiFetch(`/v1/companies/registry/resolve/${encodeURIComponent(company.gemi_number ?? "")}`, { method: "POST" })
                .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Η εισαγωγή απέτυχε."))
                .finally(() => setBusy(false));
            }}>Εισαγωγή</button> : null}
          </article>)}
          {!registry.length && <EmptyState title="Χρησιμοποιήστε επωνυμία ή ΚΑΔ για live αναζήτηση" />}
        </div>
      </section>

      <section aria-labelledby="funding-review-title">
        <div className="coverage-section-heading"><h3 id="funding-review-title">Funding match review</h3><Badge tone={funding.length ? "amber" : "green"}>{funding.length} εκκρεμείς</Badge></div>
        <div className="source-operation-list">
          {funding.map((item) => <article key={item.id}>
            <span><strong>{item.act_title ?? item.process_id ?? "Πράξη"}</strong><small>{item.funding_title} · MIS {item.mis_ops_code ?? "—"} · {Math.round(Number(item.confidence) * 100)}%</small></span>
            <Badge>{item.link_method}</Badge>
            <div className="source-review-actions">
              <button className="icon-button" type="button" title="Αποδοχή σύνδεσης" onClick={() => void review(item.id, "ACCEPTED")}><Check size={15} /></button>
              <button className="icon-button is-danger" type="button" title="Απόρριψη σύνδεσης" onClick={() => void review(item.id, "REJECTED")}><X size={15} /></button>
            </div>
          </article>)}
          {!funding.length && <EmptyState title="Δεν υπάρχουν εκκρεμείς συνδέσεις χρηματοδότησης" />}
        </div>
      </section>
      {error && <p className="form-error" role="alert">{error}</p>}
    </div>
  );
}

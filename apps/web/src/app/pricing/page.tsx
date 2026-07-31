"use client";

import { useCustom } from "@refinedev/core";
import Link from "next/link";
import { ArrowRight, Check, ShieldCheck } from "lucide-react";

import { ErrorState, LoadingState } from "@/components/procurement-ui";
import type { CommercialPlan } from "@/lib/api";

export default function PricingPage() {
  const plansQuery = useCustom<CommercialPlan[]>({ url: "/v1/commercial/plans", method: "get", queryOptions: { retry: 1 } });
  const plans = plansQuery.query.isSuccess ? plansQuery.result.data : [];
  return (
    <main className="public-product-page">
      <header className="public-product-header">
        <Link href="/" className="auth-brand"><span className="auth-brand-mark">P</span><span>Procintel<small>Procurement intelligence</small></span></Link>
        <nav><Link href="/help">Help</Link><Link href="/status">Status</Link><Link className="button button-primary" href="/login?view=signup">Δωρεάν δοκιμή</Link></nav>
      </header>
      <section className="pricing-heading"><span>PLANS</span><h1>Procurement intelligence για κάθε στάδιο ανάπτυξης</h1><p>14 ημέρες δωρεάν. Η περίοδος και τα usage limits εμφανίζονται μέσα στο workspace.</p></section>
      {plansQuery.query.isLoading ? <LoadingState label="Φόρτωση πακέτων" /> : null}
      {plansQuery.query.isError ? <ErrorState error={plansQuery.query.error} /> : null}
      <section className="pricing-grid">
        {plans.map((plan) => (
          <article key={plan.code} className={plan.code === "PROFESSIONAL" ? "is-featured" : ""}>
            <header><strong>{plan.name}</strong>{plan.code === "PROFESSIONAL" ? <span>Recommended</span> : null}</header>
            <p>{plan.description}</p>
            <div className="pricing-price">{plan.monthly_price_cents === null ? "Custom" : <><strong>{new Intl.NumberFormat("el-GR", { style: "currency", currency: plan.currency, maximumFractionDigits: 0 }).format(plan.monthly_price_cents / 100)}</strong><small>/μήνα</small></>}</div>
            <ul>
              <li><Check size={14} />{plan.entitlements.users === -1 ? "Απεριόριστοι" : plan.entitlements.users} χρήστες</li>
              <li><Check size={14} />{plan.entitlements.cpv_codes === -1 ? "Απεριόριστα" : plan.entitlements.cpv_codes} CPV</li>
              <li><Check size={14} />{plan.entitlements.ai_reports_month === -1 ? "Απεριόριστα" : plan.entitlements.ai_reports_month} intelligence reports / μήνα</li>
              <li><Check size={14} />{plan.entitlements.proposal_drafts_month === -1 ? "Απεριόριστα" : plan.entitlements.proposal_drafts_month} proposal drafts / μήνα</li>
            </ul>
            <Link className="button button-primary" href="/login?view=signup">{plan.monthly_price_cents === null ? "Μιλήστε μαζί μας" : "Έναρξη trial"}<ArrowRight size={15} /></Link>
          </article>
        ))}
      </section>
      <footer className="pricing-trust"><ShieldCheck size={16} />OIDC, tenant isolation, RBAC and audit trail</footer>
    </main>
  );
}

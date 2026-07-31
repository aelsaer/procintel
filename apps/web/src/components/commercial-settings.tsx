"use client";

import { FormEvent, useState } from "react";
import { useCustom } from "@refinedev/core";
import {
  Check,
  Circle,
  CreditCard,
  ExternalLink,
  Headphones,
  Loader2,
  Send,
} from "lucide-react";

import { Badge, EmptyState, ErrorState, LoadingState } from "@/components/procurement-ui";
import {
  apiFetch,
  type CommercialPlan,
  type SubscriptionResponse,
  type SuccessTaskResponse,
  type SupportTicketResponse,
} from "@/lib/api";
import { formatDate } from "@/lib/format";

function price(plan: CommercialPlan): string {
  if (plan.monthly_price_cents === null) return "Custom";
  return `${new Intl.NumberFormat("el-GR", { style: "currency", currency: plan.currency, maximumFractionDigits: 0 }).format(plan.monthly_price_cents / 100)}/μήνα`;
}

export function CommercialSettings() {
  const subscriptionQuery = useCustom<SubscriptionResponse>({ url: "/v1/commercial/subscription", method: "get", queryOptions: { retry: 1 } });
  const plansQuery = useCustom<CommercialPlan[]>({ url: "/v1/commercial/plans", method: "get", queryOptions: { retry: 1 } });
  const tasksQuery = useCustom<SuccessTaskResponse[]>({ url: "/v1/account-success/tasks", method: "get", queryOptions: { retry: 1 } });
  const ticketsQuery = useCustom<SupportTicketResponse[]>({ url: "/v1/support/tickets", method: "get", queryOptions: { retry: 1 } });
  const [subject, setSubject] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const subscription = subscriptionQuery.query.isSuccess ? subscriptionQuery.result.data : null;
  const plans = plansQuery.query.isSuccess ? plansQuery.result.data : [];
  const tasks = tasksQuery.query.isSuccess ? tasksQuery.result.data : [];
  const tickets = ticketsQuery.query.isSuccess ? ticketsQuery.result.data : [];

  async function run(operation: () => Promise<void>) {
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

  async function checkout(planCode: string) {
    const result = await apiFetch<{ mode: string; checkout_url: string | null; message: string }>("/v1/commercial/checkout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan_code: planCode, billing_cycle: "MONTHLY" }),
    });
    if (result.checkout_url) window.location.assign(result.checkout_url);
    else setError(result.message);
  }

  function createTicket(event: FormEvent) {
    event.preventDefault();
    if (!subject.trim() || !description.trim()) return;
    void run(async () => {
      await apiFetch("/v1/support/tickets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subject, description, category: "PRODUCT", priority: "NORMAL" }),
      });
      setSubject("");
      setDescription("");
      await ticketsQuery.query.refetch();
    });
  }

  if (subscriptionQuery.query.isLoading) return <LoadingState label="Φόρτωση συνδρομής" />;
  if (subscriptionQuery.query.isError) return <ErrorState error={subscriptionQuery.query.error} title="Δεν φορτώθηκε η συνδρομή" />;

  return (
    <div className="commercial-settings">
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      <section className="settings-band commercial-current" aria-labelledby="subscription-title">
        <div className="settings-heading">
          <div><CreditCard size={18} /><span><strong id="subscription-title">{subscription?.plan.name}</strong><small>{subscription?.status} · {subscription?.billing_provider}</small></span></div>
          <Badge tone={subscription?.status === "ACTIVE" ? "green" : "blue"}>{subscription?.status}</Badge>
        </div>
        <div className="commercial-facts">
          <span><small>Trial λήγει</small><strong>{subscription?.trial_ends_at ? formatDate(subscription.trial_ends_at) : "—"}</strong></span>
          <span><small>Τρέχουσα περίοδος</small><strong>{subscription?.current_period_end ? formatDate(subscription.current_period_end) : "—"}</strong></span>
          {["ai_reports_month", "proposal_drafts_month", "exports_month"].map((metric) => {
            const limit = subscription?.entitlements[metric];
            const usage = subscription?.usage[metric] ?? 0;
            return <span key={metric}><small>{metric.replace("_month", "").replaceAll("_", " ")}</small><strong>{usage} / {limit === -1 ? "∞" : String(limit ?? 0)}</strong></span>;
          })}
        </div>
      </section>

      <section className="commercial-plan-grid" aria-label="Available plans">
        {plans.map((plan) => (
          <article className={plan.code === subscription?.plan.code ? "is-current" : ""} key={plan.code}>
            <header><strong>{plan.name}</strong>{plan.code === subscription?.plan.code ? <Badge tone="green">Current</Badge> : null}</header>
            <p>{plan.description}</p>
            <strong className="commercial-price">{price(plan)}</strong>
            <span>{plan.entitlements.users === -1 ? "Unlimited" : plan.entitlements.users} users · {plan.entitlements.cpv_codes === -1 ? "Unlimited" : plan.entitlements.cpv_codes} CPV</span>
            {plan.code !== subscription?.plan.code ? <button className="button button-secondary" type="button" disabled={busy} onClick={() => void run(() => checkout(plan.code))}>{plan.monthly_price_cents === null ? "Επικοινωνία" : "Επιλογή"}</button> : null}
          </article>
        ))}
      </section>

      <div className="commercial-service-grid">
        <section className="settings-band" aria-labelledby="success-title">
          <div className="settings-heading"><div><Check size={18} /><span><strong id="success-title">Onboarding plan</strong><small>Managed customer success</small></span></div></div>
          <div className="success-task-list">
            {tasks.map((task) => (
              <button type="button" key={task.id} disabled={busy || task.status === "COMPLETED"} onClick={() => void run(async () => { await apiFetch(`/v1/account-success/tasks/${task.id}`, { method: "PATCH" }); await tasksQuery.query.refetch(); })}>
                {task.status === "COMPLETED" ? <Check size={16} /> : <Circle size={16} />}
                <span><strong>{task.title}</strong><small>{task.description}</small></span>
              </button>
            ))}
            {!tasks.length ? <EmptyState title="Δεν υπάρχουν onboarding tasks" /> : null}
          </div>
        </section>

        <section className="settings-band" aria-labelledby="support-title">
          <div className="settings-heading"><div><Headphones size={18} /><span><strong id="support-title">Support</strong><small>SLA-tracked requests</small></span></div><a className="icon-button" href="/help" title="Help centre"><ExternalLink size={15} /></a></div>
          <form className="support-form" onSubmit={createTicket}>
            <input value={subject} onChange={(event) => setSubject(event.target.value)} placeholder="Θέμα" aria-label="Θέμα support ticket" />
            <textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Περιγράψτε τι χρειάζεστε" rows={3} aria-label="Περιγραφή support ticket" />
            <button className="button button-primary" type="submit" disabled={busy || !subject.trim() || !description.trim()}>{busy ? <Loader2 className="spin" size={15} /> : <Send size={15} />}Αποστολή</button>
          </form>
          <div className="support-ticket-list">
            {tickets.slice(0, 5).map((ticket) => <div key={ticket.id}><span><strong>{ticket.subject}</strong><small>{ticket.category} · SLA {ticket.response_due_at ? formatDate(ticket.response_due_at) : "—"}</small></span><Badge tone={ticket.status === "RESOLVED" ? "green" : "amber"}>{ticket.status}</Badge></div>)}
          </div>
        </section>
      </div>
    </div>
  );
}

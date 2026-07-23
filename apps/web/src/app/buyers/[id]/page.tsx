"use client";

import { use } from "react";
import { useCustom, useOne } from "@refinedev/core";
import Link from "next/link";
import { BarChart3, Building2, CalendarClock, FileText, Landmark, Users } from "lucide-react";
import {
  BackLink,
  Badge,
  EmptyState,
  ErrorState,
  KeyValue,
  KeyValueList,
  LoadingState,
  MetricCard,
  PageHeader,
  Section,
} from "@/components/procurement-ui";
import type { BuyerSuppliersResponse, BuyerSummaryResponse } from "@/lib/api";
import { formatAmount } from "@/lib/format";
import { EvidenceDrawer } from "@/components/evidence-drawer";

type BuyerIntelligence = {
  identity: { contract_count: number; total_value: number | string; afm: string | null };
  top_suppliers: Array<{ id: string; canonical_name: string; value: number | string | null; contracts: number }>;
  cpv_mix: Array<{ cpv_prefix: string; acts: number; value: number | string | null }>;
  concentration: { top_supplier_share: number | string | null } | null;
  renewals: Array<{ contract_act_id: string; process_id: string; end_date: string | null; days_to_end: number | null; renewal_watch_active: boolean }>;
};

export default function BuyerPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);

  const buyerQuery = useOne<BuyerSummaryResponse>({
    resource: "buyers",
    id,
    queryOptions: { retry: 1 },
  });
  const suppliersQuery = useOne<BuyerSuppliersResponse>({
    resource: "buyer-suppliers",
    id,
    queryOptions: { retry: 1 },
  });
  const intelligenceQuery = useCustom<BuyerIntelligence>({
    url: `/v1/intelligence/buyers/${id}`,
    method: "get",
    queryOptions: { retry: 1 },
  });

  if (buyerQuery.query.isLoading) return <LoadingState label="Φόρτωση φορέα" />;
  if (buyerQuery.query.isError) return <ErrorState error={buyerQuery.query.error} />;

  const buyer = buyerQuery.result;
  if (!buyer) return <EmptyState title="Δεν βρέθηκε φορέας" />;
  const intelligence = intelligenceQuery.query.isSuccess ? intelligenceQuery.result.data : null;

  return (
    <div className="detail-layout">
      <PageHeader eyebrow="Αναθέτουσα αρχή" title={buyer.name} subtitle={buyer.id} actions={<><EvidenceDrawer objectType="entities" objectId={id} /><BackLink /></>}>
        <div className="badge-row">
          <Badge tone="blue">Buyer</Badge>
          {buyer.vat && <Badge>ΑΦΜ {buyer.vat}</Badge>}
        </div>
      </PageHeader>

      <div className="metric-grid">
        <MetricCard
          label="Συνολική αξία"
          value={formatAmount(buyer.total_contract_value)}
          icon={Landmark}
          tone="green"
        />
        <MetricCard label="Συμβάσεις" value={buyer.contract_count} icon={FileText} />
        <MetricCard label="Ανάδοχοι" value={suppliersQuery.result?.suppliers.length ?? "—"} icon={Users} tone="amber" />
        <MetricCard label="Top supplier share" value={intelligence?.concentration?.top_supplier_share !== null && intelligence?.concentration?.top_supplier_share !== undefined ? `${Math.round(Number(intelligence.concentration.top_supplier_share) * 100)}%` : "—"} icon={BarChart3} />
      </div>

      <div className="two-column intelligence-sections">
        <Section title="CPV κατανομή" eyebrow="Recorded procurement mix">
          {intelligenceQuery.query.isLoading ? <LoadingState label="Υπολογισμός CPV mix" /> : null}
          <div className="compact-list">{intelligence?.cpv_mix.map((row) => <div className="compact-row" key={row.cpv_prefix}><BarChart3 size={16} /><span>CPV {row.cpv_prefix}<small>{row.acts} πράξεις</small></span><strong>{formatAmount(row.value === null ? null : Number(row.value))}</strong></div>)}</div>
          {intelligence && !intelligence.cpv_mix.length ? <EmptyState title="Δεν υπάρχει CPV ιστορικό" /> : null}
        </Section>
        <Section title="Procurement calendar" eyebrow="Renewal watch">
          <div className="compact-list">{intelligence?.renewals.map((renewal) => <Link className="compact-row" href={`/processes/${renewal.process_id}`} key={renewal.contract_act_id}><CalendarClock size={16} /><span>{renewal.end_date ? new Intl.DateTimeFormat("el-GR", { dateStyle: "medium" }).format(new Date(renewal.end_date)) : "Χωρίς λήξη"}<small>{renewal.days_to_end ?? "—"} ημέρες έως λήξη</small></span><Badge tone={renewal.renewal_watch_active ? "amber" : "neutral"}>{renewal.renewal_watch_active ? "watch active" : "outside window"}</Badge></Link>)}</div>
          {intelligence && !intelligence.renewals.length ? <EmptyState title="Δεν υπάρχουν επερχόμενες λήξεις" /> : null}
        </Section>
      </div>

      <div className="two-column">
        <Section title="Στοιχεία">
          <KeyValueList>
            <KeyValue label="Όνομα" value={buyer.name} />
            <KeyValue label="ΑΦΜ" value={buyer.vat} />
            <KeyValue label="ID" value={buyer.id} />
          </KeyValueList>
        </Section>

        <Section title="Ανάδοχοι">
          {suppliersQuery.query.isLoading && <LoadingState label="Φόρτωση αναδόχων" />}
          {suppliersQuery.query.isError && <ErrorState error={suppliersQuery.query.error} />}
          {suppliersQuery.result && suppliersQuery.result.suppliers.length > 0 && (
            <div className="compact-list">
              {suppliersQuery.result.suppliers.map((supplier) => (
                <Link key={supplier.id} href={`/companies/${supplier.id}`} className="compact-row">
                  <Building2 size={16} aria-hidden="true" />
                  <span>{supplier.name}</span>
                  <strong>{formatAmount(supplier.value)}</strong>
                </Link>
              ))}
            </div>
          )}
          {suppliersQuery.result?.suppliers.length === 0 && <EmptyState title="Δεν βρέθηκαν ανάδοχοι" />}
        </Section>
      </div>
    </div>
  );
}

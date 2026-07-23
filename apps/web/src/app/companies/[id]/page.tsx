"use client";

import { use } from "react";
import { useCustom, useOne } from "@refinedev/core";
import Link from "next/link";
import { BarChart3, Building2, FileText, Landmark, MapPinned, Network, TrendingUp } from "lucide-react";
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
import type { CompanyContractsResponse, CompanySummaryResponse } from "@/lib/api";
import { formatAmount } from "@/lib/format";
import { EvidenceDrawer } from "@/components/evidence-drawer";

type SupplierIntelligence = {
  identity: { gemi_number: string | null; legal_form: string | null; company_status: string | null };
  buyer_dependency: Array<{ buyer_entity_id: string; buyer_name: string; value_from_buyer: number | string; total_recorded_value: number | string; dependency_ratio: number | string }>;
  market_activity: Array<{ cpv_prefix: string | null; geography: string | null; contracts: number; value: number | string | null }>;
  amendments: Array<{ contract_act_id: string; original_value: number | string | null; current_value: number | string | null; amendment_count: number; value_uplift_ratio: number | string | null }>;
};

export default function CompanyPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);

  const companyQuery = useOne<CompanySummaryResponse>({
    resource: "companies",
    id,
    queryOptions: { retry: 1 },
  });
  const contractsQuery = useOne<CompanyContractsResponse>({
    resource: "company-contracts",
    id,
    queryOptions: { retry: 1 },
  });
  const intelligenceQuery = useCustom<SupplierIntelligence>({
    url: `/v1/intelligence/suppliers/${id}`,
    method: "get",
    queryOptions: { retry: 1 },
  });

  if (companyQuery.query.isLoading) return <LoadingState label="Φόρτωση εταιρείας" />;
  if (companyQuery.query.isError) return <ErrorState error={companyQuery.query.error} />;

  const company = companyQuery.result;
  if (!company) return <EmptyState title="Δεν βρέθηκε εταιρεία" />;
  const intelligence = intelligenceQuery.query.isSuccess ? intelligenceQuery.result.data : null;
  const topDependency = intelligence?.buyer_dependency[0];

  return (
    <div className="detail-layout">
      <PageHeader eyebrow="Εταιρεία" title={company.name} subtitle={company.id} actions={<><EvidenceDrawer objectType="entities" objectId={id} /><BackLink /></>}>
        <div className="badge-row">
          <Badge tone="blue">Supplier</Badge>
          {company.company_status && <Badge tone="green">{company.company_status}</Badge>}
          {company.vat && <Badge>ΑΦΜ {company.vat}</Badge>}
        </div>
      </PageHeader>

      <div className="metric-grid">
        <MetricCard
          label="Δημόσιος τομέας"
          value={formatAmount(company.total_public_sector_value)}
          icon={Landmark}
          tone="green"
        />
        <MetricCard label="Συμβάσεις" value={company.contract_count} icon={FileText} />
        <MetricCard label="Ενεργές εγγραφές" value={contractsQuery.result?.contracts.length ?? "—"} icon={Network} tone="amber" />
        <MetricCard label="Κύριος buyer dependency" value={topDependency ? `${Math.round(Number(topDependency.dependency_ratio) * 100)}%` : "—"} detail={topDependency?.buyer_name ?? "καταγεγραμμένη αξία"} icon={BarChart3} />
      </div>

      <div className="two-column intelligence-sections">
        <Section title="Buyer dependency" eyebrow="Recorded public-sector value">
          {intelligenceQuery.query.isLoading ? <LoadingState label="Υπολογισμός buyer dependency" /> : null}
          <div className="compact-list">{intelligence?.buyer_dependency.slice(0, 8).map((row) => <Link className="compact-row" href={`/buyers/${row.buyer_entity_id}`} key={row.buyer_entity_id}><Landmark size={16} /><span>{row.buyer_name}<small>{Math.round(Number(row.dependency_ratio) * 100)}% της καταγεγραμμένης δημόσιας αξίας</small></span><strong>{formatAmount(Number(row.value_from_buyer))}</strong></Link>)}</div>
          {intelligence && !intelligence.buyer_dependency.length ? <EmptyState title="Δεν υπάρχει dependency ιστορικό" /> : null}
        </Section>
        <Section title="CPV και γεωγραφική παρουσία" eyebrow="Market footprint">
          <div className="compact-list">{intelligence?.market_activity.slice(0, 10).map((row, index) => <div className="compact-row" key={`${row.cpv_prefix}-${row.geography}-${index}`}><MapPinned size={16} /><span>{row.geography ?? "Χωρίς γεωγραφία"}<small>{row.cpv_prefix ? `CPV ${row.cpv_prefix}` : "Χωρίς CPV"} · {row.contracts} συμβάσεις</small></span><strong>{formatAmount(row.value === null ? null : Number(row.value))}</strong></div>)}</div>
        </Section>
      </div>

      <Section title="Τροποποιήσεις συμβάσεων" eyebrow="Confirmed AMENDS links">
        <div className="compact-list">{intelligence?.amendments.slice(0, 10).map((row) => <div className="compact-row" key={row.contract_act_id}><TrendingUp size={16} /><span>{row.amendment_count} τροποποιήσεις<small>{formatAmount(row.original_value === null ? null : Number(row.original_value))} → {formatAmount(row.current_value === null ? null : Number(row.current_value))}</small></span><strong>{row.value_uplift_ratio !== null ? `${Math.round(Number(row.value_uplift_ratio) * 100)}% uplift` : "—"}</strong></div>)}</div>
        {intelligence && !intelligence.amendments.length ? <EmptyState title="Δεν υπάρχουν επιβεβαιωμένες τροποποιήσεις" /> : null}
      </Section>

      <div className="two-column">
        <Section title="Στοιχεία">
          <KeyValueList>
            <KeyValue label="Όνομα" value={company.name} />
            <KeyValue label="ΑΦΜ" value={company.vat} />
            <KeyValue label="Νομική μορφή" value={company.legal_form} />
            <KeyValue label="Κατάσταση" value={company.company_status} />
          </KeyValueList>
        </Section>

        <Section title="Συμβάσεις">
          {contractsQuery.query.isLoading && <LoadingState label="Φόρτωση συμβάσεων" />}
          {contractsQuery.query.isError && <ErrorState error={contractsQuery.query.error} />}
          {contractsQuery.result && contractsQuery.result.contracts.length > 0 && (
            <div className="compact-list">
              {contractsQuery.result.contracts.map((contract) => {
                const identifier = contract.identifiers.ADAM?.[0] ?? contract.identifiers.ADA?.[0];
                const href = contract.process_id
                  ? `/processes/${contract.process_id}`
                  : identifier
                    ? `/contracts/${encodeURIComponent(identifier)}`
                    : null;
                const row = (
                  <>
                    <Building2 size={16} aria-hidden="true" />
                    <span>{contract.title ?? "(χωρίς τίτλο)"}</span>
                    <strong>{formatAmount(contract.amounts.gross, contract.amounts.currency)}</strong>
                  </>
                );

                return href ? (
                  <Link key={contract.id} href={href} className="compact-row">
                    {row}
                  </Link>
                ) : (
                  <div key={contract.id} className="compact-row">
                    {row}
                  </div>
                );
              })}
            </div>
          )}
          {contractsQuery.result?.contracts.length === 0 && <EmptyState title="Δεν βρέθηκαν συμβάσεις" />}
        </Section>
      </div>
    </div>
  );
}

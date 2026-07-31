"use client";

import { use, useState } from "react";
import { useCustom, useOne } from "@refinedev/core";
import Link from "next/link";
import { BarChart3, Building2, CalendarClock, Clock3, ExternalLink, FileText, Landmark, MapPinned, Users } from "lucide-react";
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
import { GreeceNutsMap } from "@/components/greece-nuts-map";
import { BuyerStakeholders } from "@/components/buyer-stakeholders";
import type { DecisionMakerListResponse, GeocodedLocationAnalyticsResponse, RegionAnalyticsResponse } from "@/lib/api";

type BuyerIntelligence = {
  identity: { contract_count: number; total_value: number | string; afm: string | null };
  top_suppliers: Array<{ id: string; canonical_name: string; value: number | string | null; contracts: number }>;
  cpv_mix: Array<{ cpv_prefix: string; acts: number; value: number | string | null }>;
  concentration: { top_supplier_share: number | string | null } | null;
  renewals: Array<{ contract_act_id: string; process_id: string; end_date: string | null; days_to_end: number | null; renewal_watch_active: boolean }>;
  spend_trends: Array<{ year: number; contract_count: number; total_spend: number | string; average_contract_value: number | string | null }>;
  process_duration: { request_to_notice_days: number | string | null; notice_to_award_days: number | string | null; award_to_contract_days: number | string | null; contract_to_first_payment_days: number | string | null; processes_observed: number };
  funding_projects: Array<{ id: string; mis_ops_code: string | null; title: string; program_title: string | null; budget: number | string | null; linked_acts: number }>;
  recent_notices: Array<{ id: string; process_id: string | null; title: string | null; act_type: string; event_date: string | null; official_identifier: string | null; official_url: string | null; document_url: string | null }>;
  map: Array<{ place: string | null; nuts_code: string | null; municipality_code: string | null; latitude: number | null; longitude: number | null; act_count: number; contract_value: number | string | null; confidence: number | string | null }>;
};

export default function BuyerPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [mapFocus, setMapFocus] = useState("");

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
  const stakeholderQuery = useCustom<DecisionMakerListResponse>({
    url: `/v1/buyers/${id}/decision-makers`,
    method: "get",
    queryOptions: { retry: 1 },
  });

  if (buyerQuery.query.isLoading) return <LoadingState label="Φόρτωση φορέα" />;
  if (buyerQuery.query.isError) return <ErrorState error={buyerQuery.query.error} />;

  const buyer = buyerQuery.result;
  if (!buyer) return <EmptyState title="Δεν βρέθηκε φορέας" />;
  const intelligence = intelligenceQuery.query.isSuccess ? intelligenceQuery.result.data : null;
  const mapLocations: GeocodedLocationAnalyticsResponse[] = (intelligence?.map ?? [])
    .filter((row) => row.latitude !== null && row.longitude !== null)
    .map((row) => ({
      label: row.place ?? row.nuts_code ?? "Τοποθεσία",
      nuts_code: row.nuts_code,
      municipality_name: row.place,
      regional_unit_name: null,
      region_name: null,
      latitude: Number(row.latitude),
      longitude: Number(row.longitude),
      act_count: row.act_count,
      opportunity_count: 0,
      contract_count: row.act_count,
      recorded_contract_value: row.contract_value,
      minimum_confidence: row.confidence === null ? null : Number(row.confidence),
    }));
  const mapRegions = Array.from(new Set((intelligence?.map ?? []).map((row) => row.nuts_code).filter(Boolean))).map((code) => {
    const rows = (intelligence?.map ?? []).filter((row) => row.nuts_code === code);
    return {
      nuts_code: code as string,
      region_name: rows[0]?.place ?? code as string,
      act_count: rows.reduce((sum, row) => sum + row.act_count, 0),
      opportunity_count: 0,
      notice_count: 0,
      contract_count: rows.reduce((sum, row) => sum + row.act_count, 0),
      recorded_contract_value: rows.reduce((sum, row) => sum + Number(row.contract_value ?? 0), 0),
    } satisfies RegionAnalyticsResponse;
  });

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

      <BuyerStakeholders
        response={stakeholderQuery.query.isSuccess ? stakeholderQuery.result.data : null}
        loading={stakeholderQuery.query.isLoading}
        error={stakeholderQuery.query.error}
        onRefresh={() => stakeholderQuery.query.refetch()}
      />

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

      <div className="two-column intelligence-sections">
        <Section title="Spend trends" eyebrow="Contracts by year">
          <div className="compact-list">{intelligence?.spend_trends.map((row) => <div className="compact-row" key={row.year}><BarChart3 size={16} /><span>{row.year}<small>{row.contract_count} συμβάσεις · μ.ο. {formatAmount(Number(row.average_contract_value ?? 0))}</small></span><strong>{formatAmount(Number(row.total_spend))}</strong></div>)}</div>
          {intelligence && !intelligence.spend_trends.length ? <EmptyState title="Δεν υπάρχει ιστορικό δαπανών" /> : null}
        </Section>
        <Section title="Χρόνοι διαδικασίας" eyebrow={`${intelligence?.process_duration.processes_observed ?? 0} observed processes`}>
          <div className="compact-list">
            {[
              ["Αίτημα → προκήρυξη", intelligence?.process_duration.request_to_notice_days],
              ["Προκήρυξη → ανάθεση", intelligence?.process_duration.notice_to_award_days],
              ["Ανάθεση → σύμβαση", intelligence?.process_duration.award_to_contract_days],
              ["Σύμβαση → πρώτη πληρωμή", intelligence?.process_duration.contract_to_first_payment_days],
            ].map(([label, value]) => <div className="compact-row" key={String(label)}><Clock3 size={16} /><span>{label}</span><strong>{value === null || value === undefined ? "—" : `${Math.round(Number(value))} ημέρες`}</strong></div>)}
          </div>
        </Section>
      </div>

      <div className="two-column intelligence-sections">
        <Section title="Χρηματοδοτούμενα έργα" eyebrow="ΑΝΑΠΤΥΞΗ linkage">
          <div className="compact-list">{intelligence?.funding_projects.map((project) => <div className="compact-row" key={project.id}><Landmark size={16} /><span>{project.title}<small>{project.mis_ops_code ?? project.program_title ?? "Χωρίς MIS"} · {project.linked_acts} συνδέσεις</small></span><strong>{formatAmount(project.budget === null ? null : Number(project.budget))}</strong></div>)}</div>
          {intelligence && !intelligence.funding_projects.length ? <EmptyState title="Δεν υπάρχουν συνδεδεμένα έργα" /> : null}
        </Section>
        <Section title="Πρόσφατες δημοσιεύσεις" eyebrow="Official evidence">
          <div className="compact-list">{intelligence?.recent_notices.map((notice) => <div className="compact-row" key={notice.id}><FileText size={16} /><span>{notice.title ?? notice.official_identifier ?? notice.id}<small>{notice.act_type} · {notice.event_date ?? "χωρίς ημερομηνία"}</small></span>{notice.official_url || notice.document_url ? <a href={notice.official_url ?? notice.document_url ?? "#"} target="_blank" rel="noreferrer" className="icon-button" title="Επίσημη εγγραφή"><ExternalLink size={15} /></a> : null}</div>)}</div>
          {intelligence && !intelligence.recent_notices.length ? <EmptyState title="Δεν υπάρχουν πρόσφατες δημοσιεύσεις" /> : null}
        </Section>
      </div>

      <Section title="Χάρτης δραστηριότητας φορέα" eyebrow="Geocoded procurement">
        {mapLocations.length ? <div className="buyer-map"><GreeceNutsMap focusCode={mapFocus} locations={mapLocations} regions={mapRegions} onFocus={setMapFocus} /></div> : <EmptyState title="Δεν υπάρχουν ακριβή γεωγραφικά σημεία" />}
        <div className="badge-row">{(intelligence?.map ?? []).slice(0, 12).map((row, index) => <Badge key={`${row.place}-${index}`} tone="blue"><MapPinned size={12} /> {row.place ?? row.nuts_code ?? "Τοποθεσία"} · {row.act_count}</Badge>)}</div>
      </Section>

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

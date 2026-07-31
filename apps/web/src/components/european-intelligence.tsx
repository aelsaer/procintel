"use client";

import { useMemo } from "react";
import { useCustom } from "@refinedev/core";
import Link from "next/link";
import {
  ArrowUpRight,
  CalendarClock,
  CircleAlert,
  Euro,
  ExternalLink,
  Globe2,
  Landmark,
  ShieldCheck,
} from "lucide-react";

import { Badge, EmptyState, ErrorState, LoadingState, MetricCard } from "@/components/procurement-ui";
import {
  type CrossBorderOpportunityListResponse,
  type EuropeanBenchmarkListResponse,
} from "@/lib/api";
import { activeCpvPrefixes, type BusinessScope } from "@/lib/business-scope";
import { formatAmount } from "@/lib/format";

interface CountryRollup {
  code: string;
  name: string;
  notices: number;
  awards: number;
  value: number;
  valued: number;
  deadlines: number;
}

function dateLabel(value: string | null): string {
  if (!value) return "Δεν δημοσιεύτηκε";
  return new Intl.DateTimeFormat("el-GR", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(value));
}

export function EuropeanIntelligence({ profile }: { profile: BusinessScope }) {
  const cpvPrefixes = activeCpvPrefixes(profile);
  const benchmarkQuery = useCustom<EuropeanBenchmarkListResponse>({
    url: "/v1/europe/benchmarks",
    method: "get",
    config: {
      query: {
        date_from: profile.dateFrom,
        date_to: profile.dateTo,
        cpv_prefixes: cpvPrefixes.join(","),
      },
    },
    queryOptions: { retry: 1 },
  });
  const opportunityQuery = useCustom<CrossBorderOpportunityListResponse>({
    url: "/v1/europe/opportunities",
    method: "get",
    config: {
      query: {
        date_from: profile.dateFrom,
        date_to: profile.dateTo,
        limit: 100,
      },
    },
    queryOptions: { retry: 1 },
  });
  const benchmarks = benchmarkQuery.query.isSuccess ? benchmarkQuery.result.data : null;
  const opportunities = opportunityQuery.query.isSuccess ? opportunityQuery.result.data : null;
  const matches = opportunities?.matches ?? [];
  const countries = useMemo(() => {
    const grouped = new Map<string, CountryRollup>();
    for (const row of benchmarks?.rows ?? []) {
      const current = grouped.get(row.country_code) ?? {
        code: row.country_code,
        name: row.country_name,
        notices: 0,
        awards: 0,
        value: 0,
        valued: 0,
        deadlines: 0,
      };
      current.notices += row.notice_count;
      current.awards += row.award_count;
      current.value += Number(row.total_value);
      current.valued += row.valued_notice_count;
      current.deadlines += row.deadline_notice_count;
      grouped.set(row.country_code, current);
    }
    return Array.from(grouped.values()).sort((left, right) => right.notices - left.notices);
  }, [benchmarks]);
  const totalNotices = countries.reduce((total, row) => total + row.notices, 0);
  const totalValue = countries.reduce((total, row) => total + row.value, 0);
  const maxNotices = Math.max(...countries.map((row) => row.notices), 1);

  return (
    <div className="europe-intelligence">
      <section className="analytics-metrics" aria-label="European market metrics">
        <MetricCard label="Χώρες με κάλυψη" value={benchmarks?.covered_countries ?? "—"} icon={Globe2} tone="blue" />
        <MetricCard label="TED notices" value={totalNotices || "—"} icon={Landmark} tone="neutral" />
        <MetricCard label="Διασυνοριακά matches" value={opportunityQuery.query.isSuccess ? matches.length : "—"} icon={ShieldCheck} tone="green" />
        <MetricCard label="Καταγεγραμμένη αξία" value={totalValue ? formatAmount(totalValue) : "—"} icon={Euro} tone="amber" />
      </section>

      {(benchmarkQuery.query.isLoading || opportunityQuery.query.isLoading) ? <LoadingState label="Επεξεργασία ευρωπαϊκών cohorts" /> : null}
      {benchmarkQuery.query.isError ? <ErrorState error={benchmarkQuery.query.error} title="Δεν φορτώθηκαν τα ευρωπαϊκά benchmarks" /> : null}
      {opportunityQuery.query.isError ? <ErrorState error={opportunityQuery.query.error} title="Δεν φορτώθηκαν τα cross-border matches" /> : null}

      <div className="europe-grid">
        <section className="analytics-card europe-country-panel" aria-labelledby="europe-country-title">
          <div className="section-heading compact-heading">
            <div><span className="eyebrow">TED cohorts</span><h2 id="europe-country-title">Η αγορά ανά χώρα</h2></div>
            <Badge tone="blue">{cpvPrefixes.length ? `CPV ${cpvPrefixes.join(", ")}` : "Όλα τα CPV"}</Badge>
          </div>
          {!benchmarkQuery.query.isLoading && !countries.length ? (
            <EmptyState title="Δεν υπάρχει ακόμη συγκρίσιμο TED cohort" detail="Η κάλυψη αυξάνεται από το ημερήσιο country rotation και το ευρωπαϊκό backfill." />
          ) : (
            <div className="europe-country-list">
              {countries.map((country) => (
                <article key={country.code} className="europe-country-row">
                  <div className="europe-country-name"><strong>{country.name}</strong><small>{country.code}</small></div>
                  <div className="europe-volume-track" aria-label={`${country.notices} notices`}>
                    <span style={{ width: `${Math.max((country.notices / maxNotices) * 100, 3)}%` }} />
                  </div>
                  <strong>{country.notices.toLocaleString("el-GR")}</strong>
                  <small>{country.valued}/{country.notices} με αξία · {country.deadlines} με deadline</small>
                </article>
              ))}
            </div>
          )}
        </section>

        <section className="analytics-card cross-border-panel" aria-labelledby="cross-border-title">
          <div className="section-heading compact-heading">
            <div><span className="eyebrow">Route to Europe</span><h2 id="cross-border-title">Ευκαιρίες για το προφίλ</h2></div>
            <Badge tone={matches.length ? "green" : "amber"}>{matches.length} verified matches</Badge>
          </div>
          {!opportunityQuery.query.isLoading && !matches.length ? (
            <EmptyState title="Δεν βρέθηκαν αξιόπιστα cross-border matches" detail="Δεν εμφανίζονται broad-CPV αποτελέσματα χωρίς το ειδικό λεκτικό του ενεργού προφίλ." />
          ) : (
            <div className="cross-border-list">
              {matches.map((item) => (
                <article key={item.act_id} className="cross-border-row">
                  <div className="cross-border-score"><strong>{Math.round(Number(item.match_score))}</strong><small>fit</small></div>
                  <div className="cross-border-main">
                    <div className="badge-row">
                      <Badge tone="blue">{item.country_name}</Badge>
                      {item.cpv_codes.slice(0, 2).map((code) => <Badge key={code} tone="neutral">CPV {code}</Badge>)}
                    </div>
                    <h3>{item.title}</h3>
                    <p>{item.buyer_name ?? "Μη ταυτοποιημένος αναθέτων φορέας"}</p>
                    <div className="cross-border-facts">
                      <span><Euro size={13} />{item.estimated_value ? formatAmount(Number(item.estimated_value)) : "Χωρίς εκτίμηση"}</span>
                      <span><CalendarClock size={13} />{dateLabel(item.submission_deadline)}</span>
                    </div>
                    <div className="cross-border-evidence">
                      {item.reasons.slice(0, 2).map((reason) => <span key={reason}><ShieldCheck size={12} />{reason}</span>)}
                      {item.barriers.slice(0, 2).map((barrier) => <span className="is-barrier" key={barrier}><CircleAlert size={12} />{barrier}</span>)}
                    </div>
                  </div>
                  <div className="cross-border-actions">
                    <a className="icon-button" href={item.official_url} target="_blank" rel="noreferrer" title="Επίσημη εγγραφή TED"><ExternalLink size={16} /></a>
                    {item.process_id ? <Link className="icon-button" href={`/processes/${item.process_id}`} title="Συνδεδεμένη διαδικασία"><ArrowUpRight size={16} /></Link> : null}
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      </div>

      <div className="framework-methodology">
        {(benchmarks?.methodology ?? opportunities?.methodology ?? []).map((line) => <span key={line}>{line}</span>)}
      </div>
    </div>
  );
}

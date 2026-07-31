"use client";

import { useMemo, useState } from "react";
import { useCustom } from "@refinedev/core";
import Link from "next/link";
import {
  ArrowUpRight,
  BellOff,
  BellPlus,
  Building2,
  CalendarClock,
  ExternalLink,
  Network,
  RefreshCw,
  Route,
  UsersRound,
} from "lucide-react";

import { Badge, EmptyState, ErrorState, LoadingState, MetricCard } from "@/components/procurement-ui";
import {
  activeCpvPrefixes,
  activeKeywords,
  workspaceFilterQuery,
  type BusinessScope,
} from "@/lib/business-scope";
import { api, type FrameworkListResponse, type FrameworkResponse } from "@/lib/api";
import { formatAmount } from "@/lib/format";

type FrameworkMode = "all" | "active" | "reopening" | "watched";

const statusLabels: Record<FrameworkResponse["status"], string> = {
  ACTIVE: "Ενεργή",
  REOPENING: "Λήγει σύντομα",
  EXPIRED: "Έληξε",
  UNKNOWN: "Άγνωστη λήξη",
};

export function FrameworksWorkspace({ profile }: { profile: BusinessScope }) {
  const [mode, setMode] = useState<FrameworkMode>("all");
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const query = useCustom<FrameworkListResponse>({
    url: "/v1/frameworks",
    method: "get",
    config: {
      query: {
        cpv_prefixes: activeCpvPrefixes(profile).join(","),
        keywords: activeKeywords(profile).join(","),
        excluded_cpv_prefixes: profile.excludedCpvPrefixes.join(","),
        ...workspaceFilterQuery(profile),
        limit: 80,
      },
    },
    queryOptions: { retry: 1 },
  });
  const response = query.query.isSuccess ? query.result.data : null;
  const frameworks = useMemo(() => {
    const rows = response?.frameworks ?? [];
    if (mode === "active") return rows.filter((item) => item.status === "ACTIVE");
    if (mode === "reopening") return rows.filter((item) => item.status === "REOPENING");
    if (mode === "watched") return rows.filter((item) => item.watched);
    return rows;
  }, [mode, response]);

  async function toggleWatch(item: FrameworkResponse) {
    setPendingId(item.act_id);
    setActionError(null);
    try {
      if (item.watched) await api.unwatchFramework(item.act_id);
      else await api.watchFramework(item.act_id, 90);
      await query.query.refetch();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Η watchlist δεν ενημερώθηκε.");
    } finally {
      setPendingId(null);
    }
  }

  return (
    <div className="view-stack framework-view">
      <div className="view-heading">
        <div>
          <span className="eyebrow">Route to market</span>
          <h1>Συμφωνίες-πλαίσιο</h1>
        </div>
        <button className="icon-button" type="button" onClick={() => void query.query.refetch()} title="Ανανέωση">
          <RefreshCw size={17} aria-hidden="true" />
        </button>
      </div>

      <section className="framework-summary" aria-label="Framework metrics">
        <MetricCard label="Συμφωνίες" value={response?.summary.framework_count ?? "—"} icon={Route} tone="blue" />
        <MetricCard label="Λήγουν σύντομα" value={response?.summary.reopening_count ?? "—"} icon={CalendarClock} tone="amber" />
        <MetricCard label="Call-off spend" value={formatAmount(Number(response?.summary.realized_spend ?? 0))} icon={Network} tone="green" />
        <MetricCard label="Listed suppliers" value={response?.summary.supplier_count ?? "—"} icon={UsersRound} />
      </section>

      <div className="framework-toolbar">
        <div className="segmented-control" aria-label="Κατάσταση συμφωνιών">
          {([
            ["all", "Όλες"],
            ["active", "Ενεργές"],
            ["reopening", "Λήγουν"],
            ["watched", "Watchlist"],
          ] as Array<[FrameworkMode, string]>).map(([value, label]) => (
            <button type="button" key={value} className={mode === value ? "is-active" : ""} onClick={() => setMode(value)}>
              {label}
            </button>
          ))}
        </div>
        <p><strong>Ceiling:</strong> {formatAmount(Number(response?.summary.ceiling_value ?? 0))} · δεν προσμετράται ως δαπάνη</p>
      </div>

      {actionError ? <p className="form-error" role="alert">{actionError}</p> : null}
      {query.query.isLoading ? <LoadingState label="Ανάλυση συμφωνιών-πλαίσιο" /> : null}
      {query.query.isError ? <ErrorState error={query.query.error} title="Δεν είναι διαθέσιμες οι συμφωνίες-πλαίσιο" /> : null}
      {!query.query.isLoading && response && !frameworks.length ? (
        <EmptyState title="Δεν βρέθηκαν συμφωνίες για το ενεργό προφίλ" detail="Η προβολή ακολουθεί τα κοινά CPV, λεκτικά, γεωγραφία και ημερομηνίες." />
      ) : null}

      {frameworks.length ? (
        <div className="framework-list">
          {frameworks.map((item) => (
            <article className="framework-row" key={item.act_id}>
              <div className="framework-score">
                <strong>{Math.round(item.relevance_score)}</strong>
                <small>fit</small>
              </div>
              <div className="framework-main">
                <div className="badge-row">
                  <Badge tone={item.status === "REOPENING" ? "amber" : item.status === "ACTIVE" ? "green" : "neutral"}>
                    {statusLabels[item.status]}
                  </Badge>
                  {item.cpv_codes.slice(0, 3).map((code) => <Badge tone="blue" key={code}>CPV {code}</Badge>)}
                </div>
                <h2>{item.title}</h2>
                <p><Building2 size={14} />{item.buyer_name ?? "Μη ταυτοποιημένος φορέας"}</p>
                <div className="framework-economics">
                  <span><small>Πραγματική δαπάνη</small><strong>{formatAmount(Number(item.realized_spend))}</strong></span>
                  <span><small>Ceiling</small><strong>{formatAmount(Number(item.ceiling_amount ?? 0))}</strong></span>
                  <span><small>Call-offs</small><strong>{item.call_off_count}</strong></span>
                  <span><small>Λήξη</small><strong>{item.valid_until ?? "—"}</strong></span>
                </div>
                <div className="framework-suppliers">
                  <small>Listed suppliers</small>
                  <div>
                    {item.suppliers.slice(0, 6).map((supplier) => (
                      <Link href={`/companies/${supplier.entity_id}`} key={`${supplier.entity_id}-${supplier.lot_identifier ?? ""}`}>
                        {supplier.name}
                      </Link>
                    ))}
                    {!item.suppliers.length ? <span>Δεν έχουν δημοσιευτεί ανάδοχοι</span> : null}
                    {item.suppliers.length > 6 ? <span>+{item.suppliers.length - 6}</span> : null}
                  </div>
                </div>
              </div>
              <div className="framework-actions">
                <button
                  className={`icon-button ${item.watched ? "is-active" : ""}`}
                  type="button"
                  onClick={() => void toggleWatch(item)}
                  disabled={pendingId === item.act_id}
                  title={item.watched ? "Αφαίρεση από watchlist" : "Ειδοποίηση πριν τη λήξη"}
                  aria-label={item.watched ? `Αφαίρεση ${item.title} από watchlist` : `Παρακολούθηση ${item.title}`}
                >
                  {item.watched ? <BellOff size={16} /> : <BellPlus size={16} />}
                </button>
                {item.official_url ? <a className="icon-button" href={item.official_url} target="_blank" rel="noreferrer" title="Επίσημη εγγραφή"><ExternalLink size={16} /></a> : null}
                {item.process_id ? <Link className="icon-button" href={`/processes/${item.process_id}`} title="Άνοιγμα διαδικασίας"><ArrowUpRight size={16} /></Link> : null}
              </div>
            </article>
          ))}
        </div>
      ) : null}

      {response ? <div className="framework-methodology">{response.methodology.map((line) => <span key={line}>{line}</span>)}</div> : null}
    </div>
  );
}

"use client";

import { useEffect, useMemo, useState } from "react";
import { useCustom } from "@refinedev/core";
import Link from "next/link";
import {
  Building2,
  CalendarClock,
  CheckCircle2,
  Eye,
  EyeOff,
  GitCompareArrows,
  Landmark,
  Network,
  RefreshCw,
  ShieldCheck,
  Target,
  Trophy,
  UsersRound,
} from "lucide-react";
import { Badge, EmptyState, ErrorState, LoadingState } from "@/components/procurement-ui";
import type {
  CompetitorDiscoveryResponse,
  CompetitorProfileResponse,
  CompetitorSummary,
} from "@/lib/api";
import { api } from "@/lib/api";

type Query = Record<string, string | number>;

function toNumber(value: number | string | null | undefined): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function compactCurrency(value: number | string | null | undefined): string {
  return new Intl.NumberFormat("el-GR", {
    style: "currency",
    currency: "EUR",
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(toNumber(value));
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "χωρίς ημερομηνία";
  return new Intl.DateTimeFormat("el-GR", { dateStyle: "medium" }).format(new Date(value));
}

function evidenceBadge(competitor: CompetitorSummary) {
  if (competitor.bid_count > 0) return <Badge tone="blue">Επιβεβαιωμένη συμμετοχή</Badge>;
  return <Badge tone="green">Επιβεβαιωμένος ανάδοχος</Badge>;
}

function CompetitorRow({
  competitor,
  active,
  watched,
  onSelect,
  onWatch,
}: {
  competitor: CompetitorSummary;
  active: boolean;
  watched: boolean;
  onSelect: () => void;
  onWatch: () => void;
}) {
  return (
    <article className={`competitor-row${active ? " is-active" : ""}`}>
      <button className="competitor-select" type="button" onClick={onSelect} aria-pressed={active}>
        <span className="competitor-score" aria-label={`Συνάφεια market ${Math.round(competitor.similarity_score)} στα 100`}>
          <strong>{Math.round(competitor.similarity_score)}</strong>
          <small>fit</small>
        </span>
        <span className="competitor-copy">
          <span className="competitor-badges">
            {evidenceBadge(competitor)}
            <Badge tone="amber">Market inference {Math.round(competitor.similarity_score)}%</Badge>
          </span>
          <strong>{competitor.name}</strong>
          <small>
            {competitor.afm ? `ΑΦΜ ${competitor.afm}` : "Χωρίς ΑΦΜ"} · {competitor.award_count} αναθέσεις · {competitor.buyer_count} φορείς
          </small>
        </span>
        <span className="competitor-value">
          <strong>{compactCurrency(competitor.recorded_value)}</strong>
          <small>{formatDate(competitor.last_activity)}</small>
        </span>
      </button>
      <button
        className="competitor-watch"
        type="button"
        onClick={onWatch}
        aria-label={watched ? `Αφαίρεση ${competitor.name} από παρακολούθηση` : `Παρακολούθηση ${competitor.name}`}
        title={watched ? "Διακοπή παρακολούθησης" : "Παρακολούθηση εταιρείας"}
      >
        {watched ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
      </button>
    </article>
  );
}

function Breakdown({
  title,
  rows,
  icon: Icon,
}: {
  title: string;
  rows: CompetitorProfileResponse["top_buyers"];
  icon: typeof Landmark;
}) {
  const maximum = Math.max(...rows.map((row) => row.count), 1);
  return (
    <section className="competitor-breakdown">
      <h3><Icon size={15} aria-hidden="true" /> {title}</h3>
      {rows.slice(0, 5).map((row) => (
        <div className="breakdown-row" key={row.key}>
          <span><strong>{row.label}</strong><small>{row.count} διαδικασίες</small></span>
          <i style={{ width: `${Math.max((row.count / maximum) * 100, 5)}%` }} />
        </div>
      ))}
      {!rows.length && <p className="muted-inline">Δεν υπάρχουν διαθέσιμα στοιχεία.</p>}
    </section>
  );
}

export function CompetitorsWorkspace({
  query,
  historicalQuery,
  globalQuery,
  referenceAfm,
  scopeLabel,
}: {
  query: Query;
  historicalQuery: Query;
  globalQuery: Query;
  referenceAfm: string;
  scopeLabel: string;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [watched, setWatched] = useState<Map<string, string>>(new Map());
  const [scopeMode, setScopeMode] = useState<"period" | "history" | "global">("period");

  useEffect(() => {
    void api.getWatches("COMPETITOR").then((items) => {
      setWatched(new Map(items.map((item) => [item.object_id, item.id])));
    });
  }, []);

  const discovery = useCustom<CompetitorDiscoveryResponse>({
    url: "/v1/competitors/discover",
    method: "get",
    config: { query },
    queryOptions: { retry: 1 },
  });
  const historicalDiscovery = useCustom<CompetitorDiscoveryResponse>({
    url: "/v1/competitors/discover",
    method: "get",
    config: { query: historicalQuery },
    queryOptions: { retry: 1, enabled: scopeMode === "history" },
  });
  const globalDiscovery = useCustom<CompetitorDiscoveryResponse>({
    url: "/v1/competitors/discover",
    method: "get",
    config: { query: globalQuery },
    queryOptions: { retry: 1, enabled: scopeMode === "global" },
  });

  const data = scopeMode === "global"
    ? globalDiscovery.query.isSuccess ? globalDiscovery.result.data : null
    : scopeMode === "history"
      ? historicalDiscovery.query.isSuccess ? historicalDiscovery.result.data : null
      : discovery.query.isSuccess ? discovery.result.data : null;
  const competitors = useMemo(() => data?.competitors ?? [], [data]);

  const effectiveSelectedId =
    selectedId && competitors.some((item) => item.company_id === selectedId)
      ? selectedId
      : competitors[0]?.company_id ?? null;

  const profileQuery = useCustom<CompetitorProfileResponse>({
    url: effectiveSelectedId ? `/v1/competitors/${effectiveSelectedId}` : "/v1/competitors/none",
    method: "get",
    config: { query: referenceAfm ? { reference_afm: referenceAfm } : {} },
    queryOptions: { enabled: Boolean(effectiveSelectedId), retry: 1 },
  });
  const profile = profileQuery.query.isSuccess ? profileQuery.result.data : null;

  async function toggleWatch(companyId: string) {
    const watchId = watched.get(companyId);
    if (watchId) {
      await api.deleteWatch(watchId);
      setWatched((items) => {
        const next = new Map(items);
        next.delete(companyId);
        return next;
      });
    } else {
      const created = await api.createWatch(companyId, "COMPETITOR");
      setWatched((items) => new Map(items).set(created.object_id, created.id));
    }
  }

  const activeDiscovery = scopeMode === "global"
    ? globalDiscovery
    : scopeMode === "history" ? historicalDiscovery : discovery;
  const loading = activeDiscovery.query.isLoading;
  const failed = activeDiscovery.query.isError;

  return (
    <div className="view-stack competitors-view">
      <div className="view-heading competitor-heading">
        <div>
          <span className="eyebrow">Competitive intelligence</span>
          <h1>Ανταγωνιστικό τοπίο</h1>
          <p>Ποιοι δραστηριοποιούνται στο market, πού κερδίζουν και με ποιους φορείς έχουν σχέση.</p>
        </div>
        <div className="view-heading-actions">
          <div className="segmented-control" aria-label="Χρονικό scope ανταγωνισμού">
            <button type="button" className={scopeMode === "history" ? "is-active" : ""} onClick={() => { setScopeMode("history"); setSelectedId(null); }}>Ανάδοχοι market</button>
            <button type="button" className={scopeMode === "period" ? "is-active" : ""} onClick={() => { setScopeMode("period"); setSelectedId(null); }}>Τρέχον διάστημα</button>
            <button type="button" className={scopeMode === "global" ? "is-active" : ""} onClick={() => { setScopeMode("global"); setSelectedId(null); }}>Όλη η βάση</button>
          </div>
          <button
            className="icon-button"
            type="button"
            onClick={() => activeDiscovery.query.refetch()}
            aria-label="Ανανέωση ανταγωνισμού"
            title="Ανανέωση δεδομένων"
          >
            <RefreshCw size={17} aria-hidden="true" />
          </button>
        </div>
      </div>

      <section className="competition-summary" aria-label="Σύνοψη ανταγωνισμού">
        <div><UsersRound size={17} /><span><strong>{data?.coverage.companies_found ?? 0}</strong><small>εταιρείες στο radar</small></span></div>
        <div><CheckCircle2 size={17} /><span><strong>{data?.coverage.confirmed_bidder_facts ?? 0}</strong><small>τεκμηριωμένες συμμετοχές</small></span></div>
        <div><Trophy size={17} /><span><strong>{data?.coverage.confirmed_winner_facts ?? 0}</strong><small>τεκμηριωμένες αναθέσεις</small></span></div>
        <div><Eye size={17} /><span><strong>{watched.size}</strong><small>σε παρακολούθηση</small></span></div>
      </section>

      <div className="competition-scope">
        <Target size={15} aria-hidden="true" />
        <span>{scopeMode === "global"
          ? "Ρητή διερεύνηση ολόκληρης της φορτωμένης βάσης, χωρίς φίλτρα προφίλ."
          : scopeMode === "history"
            ? `Ιστορικοί ανάδοχοι και συμμετέχοντες σε αντίστοιχο CPV ή τίτλο · ${data?.scope.cpv_prefixes.length || "Όλα"} CPV · ${data?.scope.keywords.join(", ") || "όλα τα αντικείμενα"}`
            : scopeLabel}</span>
        <Badge tone={scopeMode === "history" ? "blue" : "amber"}>{scopeMode === "global" ? "Συνολική βάση" : scopeMode === "history" ? "Ανάδοχοι market" : "Τρέχον διάστημα"}</Badge>
      </div>

      {failed && <ErrorState title="Δεν είναι διαθέσιμη η ανάλυση ανταγωνισμού" error={activeDiscovery.query.error} />}
      {loading && <LoadingState label="Ανάλυση ανταγωνιστικού market" />}
      {!loading && !failed && !competitors.length && (
        <EmptyState
          title={`Δεν βρέθηκαν εταιρείες στο ${scopeMode === "global" ? "φορτωμένο αρχείο" : scopeMode === "history" ? "ιστορικό" : "επιλεγμένο διάστημα"}`}
          detail={scopeMode === "period"
            ? "Επίλεξε Ανάδοχοι market για το ίδιο CPV/keyword/γεωγραφικό προφίλ χωρίς ημερομηνίες. Δεν προβάλλουμε άσχετη συνολική αγορά."
            : scopeMode === "history"
              ? "Διεύρυνε CPV, λέξεις-κλειδιά ή περιοχή στο Προφίλ, ή επίλεξε ρητά Όλη η βάση."
              : "Δεν υπάρχουν ακόμη επιβεβαιωμένοι ανάδοχοι ή συμμετέχοντες στο φορτωμένο αρχείο."}
        />
      )}

      {competitors.length > 0 && (
        <div className="competition-grid">
          <section className="competitor-roster" aria-labelledby="competitor-roster-title">
            <div className="panel-heading">
              <div><span className="eyebrow">Market roster</span><h2 id="competitor-roster-title">Εταιρείες με σχετική δραστηριότητα</h2></div>
              <Badge>{competitors.length}</Badge>
            </div>
            <div className="competitor-list">
              {competitors.map((competitor) => (
                <CompetitorRow
                  key={competitor.company_id}
                  competitor={competitor}
                  active={effectiveSelectedId === competitor.company_id}
                  watched={watched.has(competitor.company_id)}
                  onSelect={() => setSelectedId(competitor.company_id)}
                  onWatch={() => void toggleWatch(competitor.company_id)}
                />
              ))}
            </div>
          </section>

          <aside className="competitor-dossier" aria-label="Φάκελος ανταγωνιστή">
            {profileQuery.query.isLoading && <LoadingState label="Άνοιγμα εταιρικού φακέλου" />}
            {profileQuery.query.isError && <ErrorState title="Δεν είναι διαθέσιμος ο εταιρικός φάκελος" error={profileQuery.query.error} />}
            {profile && (
              <>
                <header className="dossier-heading">
                  <span className="dossier-mark" aria-hidden="true"><Building2 size={20} /></span>
                  <div><span className="eyebrow">Competitor dossier</span><h2>{profile.name}</h2><p>{profile.afm ? `ΑΦΜ ${profile.afm}` : "Χωρίς διαθέσιμο ΑΦΜ"}{profile.gemi_number ? ` · ΓΕΜΗ ${profile.gemi_number}` : ""}</p></div>
                  <Link href={`/companies/${profile.company_id}`} className="icon-button" aria-label="Άνοιγμα πλήρους εταιρικού προφίλ" title="Πλήρες εταιρικό προφίλ"><Network size={16} /></Link>
                </header>
                <div className="dossier-metrics">
                  <span><strong>{profile.metrics.award_count}</strong><small>αναθέσεις</small></span>
                  <span><strong>{profile.metrics.bid_count}</strong><small>συμμετοχές</small></span>
                  <span><strong>{compactCurrency(profile.metrics.recorded_value)}</strong><small>αξία</small></span>
                  <span><strong>{profile.metrics.head_to_head_count}</strong><small>head-to-head</small></span>
                </div>
                <div className="dossier-breakdowns">
                  <Breakdown title="Κύριοι αγοραστές" rows={profile.top_buyers} icon={Landmark} />
                  <Breakdown title="CPV footprint" rows={profile.cpv_distribution} icon={GitCompareArrows} />
                </div>
                <section className="competitor-activity">
                  <h3><CalendarClock size={15} aria-hidden="true" /> Πρόσφατη δραστηριότητα</h3>
                  {profile.recent_activity.slice(0, 5).map((activity) => (
                    <Link href={`/processes/${activity.process_id}`} key={activity.activity_id}>
                      <span className={`activity-role role-${activity.role.toLowerCase()}`}>{activity.role === "WINNER" ? <Trophy size={14} /> : <ShieldCheck size={14} />}</span>
                      <span><strong>{activity.title ?? activity.public_id}</strong><small>{activity.buyer_name ?? "Άγνωστος φορέας"} · {formatDate(activity.event_date)}</small></span>
                      <span className="activity-value">{compactCurrency(activity.value)}</span>
                    </Link>
                  ))}
                  {!profile.recent_activity.length && <p className="muted-inline">Δεν υπάρχει πρόσφατη δραστηριότητα.</p>}
                </section>
              </>
            )}
          </aside>
        </div>
      )}

      {data && <p className="competition-method"><ShieldCheck size={14} /> {data.coverage.source_note}</p>}
    </div>
  );
}

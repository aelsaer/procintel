"use client";

import { FormEvent, useEffect, useState } from "react";
import { Bell, BellRing, Check, Mail, Pause, Pencil, Play, Plus, Save, Trash2, Webhook, X } from "lucide-react";
import { api, type AlertDeliveryHistoryResponse, type AlertDigestHistoryResponse, type AlertEventResponse, type AlertRuleResponse } from "@/lib/api";
import { activeCpvPrefixes, activeKeywords, type BusinessScope } from "@/lib/business-scope";
import { Badge, EmptyState, ErrorState, LoadingState } from "@/components/procurement-ui";

export function AlertsWorkspace({ profile }: { profile: BusinessScope }) {
  const [rules, setRules] = useState<AlertRuleResponse[]>([]);
  const [events, setEvents] = useState<AlertEventResponse[]>([]);
  const [digests, setDigests] = useState<AlertDigestHistoryResponse[]>([]);
  const [deliveries, setDeliveries] = useState<AlertDeliveryHistoryResponse[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("Νέες ευκαιρίες για το εταιρικό προφίλ");
  const [schedule, setSchedule] = useState("DAILY_DIGEST");
  const [digestTime, setDigestTime] = useState("08:00");
  const [channel, setChannel] = useState("EMAIL");
  const [target, setTarget] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function refresh() {
    setLoading(true);
    try {
      const [nextRules, nextEvents, nextDigests, nextDeliveries] = await Promise.all([api.getAlertRules(), api.getAlertEvents(), api.getAlertDigests(), api.getAlertDeliveries()]);
      setRules(nextRules);
      setEvents(nextEvents);
      setDigests(nextDigests);
      setDeliveries(nextDeliveries);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let active = true;
    void Promise.all([api.getAlertRules(), api.getAlertEvents(), api.getAlertDigests(), api.getAlertDeliveries()]).then(([nextRules, nextEvents, nextDigests, nextDeliveries]) => {
      if (!active) return;
      setRules(nextRules); setEvents(nextEvents); setDigests(nextDigests); setDeliveries(nextDeliveries); setLoading(false);
    }).catch((nextError) => { if (active) { setError(nextError); setLoading(false); } });
    return () => { active = false; };
  }, []);

  function bodyFor(rule?: AlertRuleResponse, preserveRule = false) {
    const cpvPrefixes = activeCpvPrefixes(profile);
    const keywords = activeKeywords(profile);
    const filters = rule?.filters ?? {
      ...(keywords.length ? { keywords } : {}),
      ...(cpvPrefixes.length ? { cpv_prefixes: cpvPrefixes } : {}),
      ...(keywords.length ? { taxonomy_match_mode: "KEYWORD_REQUIRED" } : {}),
      ...(!keywords.length && cpvPrefixes.length ? { taxonomy_match_any: true } : {}),
      ...(profile.nutsCode ? { nuts_code: profile.nutsCode } : {}),
      ...(profile.municipality ? { municipality: profile.municipality } : {}),
      ...(Number(profile.amountMin) > 0 ? { amount_min: Number(profile.amountMin) } : {}),
    };
    const targets = preserveRule
      ? (rule?.targets ?? [])
      : target ? [{ channel_type: channel, target, is_active: true }] : [];
    const channels = ["IN_APP", ...Array.from(new Set(targets.map((item) => item.channel_type)))];
    return {
      name: preserveRule ? (rule?.name ?? name) : name,
      event_types: rule?.event_types ?? ["opportunity.created", "opportunity.updated"],
      filters,
      schedule: preserveRule ? (rule?.schedule ?? schedule) : schedule,
      delivery_channels: channels,
      timezone: rule?.timezone ?? "Europe/Athens",
      digest_time: preserveRule ? (rule?.digest_time ?? "08:00:00") : `${digestTime}:00`,
      is_active: rule?.is_active ?? true,
      targets: targets.map((target) => ({ channel_type: target.channel_type, target: target.target, is_active: target.is_active })),
    };
  }

  function resetEditor() {
    setEditingId(null);
    setName("Νέες ευκαιρίες για το εταιρικό προφίλ");
    setSchedule("DAILY_DIGEST");
    setDigestTime("08:00");
    setChannel("EMAIL");
    setTarget("");
  }

  function editRule(rule: AlertRuleResponse) {
    setEditingId(rule.id);
    setName(rule.name);
    setSchedule(rule.schedule);
    setDigestTime(rule.digest_time.slice(0, 5));
    setChannel(rule.targets[0]?.channel_type ?? "EMAIL");
    setTarget(rule.targets[0]?.target ?? "");
  }

  async function saveRule(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    try {
      const current = rules.find((rule) => rule.id === editingId);
      if (current) {
        const updated = await api.updateAlertRule(current.id, bodyFor(current));
        setRules((items) => items.map((item) => item.id === updated.id ? updated : item));
      } else {
        const created = await api.createAlertRule(bodyFor());
        setRules((items) => [created, ...items]);
      }
      resetEditor();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setSaving(false);
    }
  }

  async function toggleRule(rule: AlertRuleResponse) {
    const updated = await api.updateAlertRule(rule.id, { ...bodyFor(rule, true), is_active: !rule.is_active });
    setRules((items) => items.map((item) => item.id === updated.id ? updated : item));
  }

  async function archiveRule(rule: AlertRuleResponse) {
    await api.archiveAlertRule(rule.id);
    setRules((items) => items.filter((item) => item.id !== rule.id));
  }

  async function markRead(event: AlertEventResponse) {
    if (!event.read_at) await api.markAlertRead(event.id);
    await refresh();
  }

  return (
    <div className="view-stack alerts-workspace">
      <div className="view-heading">
        <div><span className="eyebrow">Alert operations</span><h1>Κανόνες και ειδοποιήσεις</h1></div>
        <Badge tone="green">{rules.filter((rule) => rule.is_active).length} ενεργοί</Badge>
      </div>

      {error ? <ErrorState title="Δεν φορτώθηκαν τα alerts" error={error} /> : null}
      {loading ? <LoadingState label="Φόρτωση κανόνων και ιστορικού" /> : null}

      <div className="alert-operations-grid">
        <section className="alert-rule-editor" aria-labelledby="new-alert-title">
          <div className="panel-heading"><div><span className="eyebrow">{editingId ? "Επεξεργασία κανόνα" : "Νέος κανόνας"}</span><h2 id="new-alert-title">Από το ενεργό προφίλ</h2></div>{editingId ? <button className="icon-button" type="button" onClick={resetEditor} aria-label="Ακύρωση επεξεργασίας"><X size={16} /></button> : <BellRing size={18} />}</div>
          <form onSubmit={saveRule} className="alert-rule-form">
            <label><span>Όνομα</span><input value={name} onChange={(event) => setName(event.target.value)} required /></label>
            <label><span>Συχνότητα</span><select value={schedule} onChange={(event) => setSchedule(event.target.value)}><option value="IMMEDIATE">Άμεσα</option><option value="DAILY_DIGEST">Ημερήσιο digest</option><option value="WEEKLY_DIGEST">Εβδομαδιαίο digest</option></select></label>
            {schedule !== "IMMEDIATE" && <label><span>Ώρα digest</span><input type="time" value={digestTime} onChange={(event) => setDigestTime(event.target.value)} /></label>}
            <label><span>Κανάλι παράδοσης</span><select value={channel} onChange={(event) => setChannel(event.target.value)}><option value="EMAIL">Email</option><option value="WEBHOOK">Webhook</option><option value="TEAMS">Microsoft Teams</option><option value="SLACK">Slack</option></select></label>
            <label><span>Προορισμός, προαιρετικός</span><input type={channel === "EMAIL" ? "email" : "url"} value={target} onChange={(event) => setTarget(event.target.value)} placeholder={channel === "EMAIL" ? "sales@example.gr" : "https://…"} /></label>
            <div className="active-filter-strip">
              <span>{activeCpvPrefixes(profile).length ? `${activeCpvPrefixes(profile).length} CPV` : "Όλα τα CPV"}</span>
              <span>{profile.nutsCode || "Ελλάδα"}</span>
              <span>{activeKeywords(profile).join(", ") || "όλα τα αντικείμενα"}</span>
            </div>
            <button className="button button-primary" type="submit" disabled={saving}>{editingId ? <Save size={16} /> : <Plus size={16} />}{saving ? "Αποθήκευση" : editingId ? "Ενημέρωση κανόνα" : "Δημιουργία κανόνα"}</button>
          </form>
        </section>

        <section className="alert-rules-list" aria-labelledby="saved-alerts-title">
          <div className="panel-heading"><div><span className="eyebrow">Persisted rules</span><h2 id="saved-alerts-title">Αποθηκευμένοι κανόνες</h2></div><Bell size={18} /></div>
          {rules.map((rule) => (
            <article key={rule.id} className="alert-rule-row">
              <div><span className={`rule-state${rule.is_active ? " is-active" : ""}`} /><strong>{rule.name}</strong><small>{rule.schedule.replaceAll("_", " ")} · {rule.event_count} events · {rule.unread_count} unread</small></div>
              <div className="rule-channels">{rule.delivery_channels.map((channel) => <span key={channel}>{channel === "EMAIL" ? <Mail size={13} /> : channel === "WEBHOOK" ? <Webhook size={13} /> : <Bell size={13} />}{channel}</span>)}</div>
              <div className="row-actions"><button className="icon-button" type="button" onClick={() => editRule(rule)} aria-label="Επεξεργασία κανόνα"><Pencil size={15} /></button><button className="icon-button" type="button" onClick={() => void toggleRule(rule)} aria-label={rule.is_active ? "Παύση κανόνα" : "Ενεργοποίηση κανόνα"}>{rule.is_active ? <Pause size={15} /> : <Play size={15} />}</button><button className="icon-button" type="button" onClick={() => void archiveRule(rule)} aria-label="Αρχειοθέτηση κανόνα"><Trash2 size={15} /></button></div>
            </article>
          ))}
          {!rules.length && !loading ? <EmptyState title="Δεν υπάρχουν κανόνες" detail="Δημιούργησε τον πρώτο κανόνα από το ενεργό business profile." /> : null}
        </section>
      </div>

      <section className="alert-inbox" aria-labelledby="alert-inbox-title">
        <div className="panel-heading"><div><span className="eyebrow">Digest history</span><h2 id="alert-inbox-title">Inbox ειδοποιήσεων</h2></div><Badge>{events.filter((event) => !event.read_at).length} νέα</Badge></div>
        <div className="alert-event-list">
          {events.slice(0, 12).map((event) => (
            <button key={event.id} type="button" className={event.read_at ? "is-read" : ""} onClick={() => void markRead(event)}>
              <span className="event-status">{event.read_at ? <Check size={15} /> : <BellRing size={15} />}</span>
              <span><strong>{event.rule_name}</strong><small>{event.event_type} · {new Intl.DateTimeFormat("el-GR", { dateStyle: "medium", timeStyle: "short" }).format(new Date(event.triggered_at))}</small></span>
            </button>
          ))}
          {!events.length ? <EmptyState title="Καμία ειδοποίηση ακόμη" detail="Τα νέα material changes θα εμφανίζονται εδώ χωρίς διπλότυπα." /> : null}
        </div>
      </section>

      <section className="alert-inbox alert-history" aria-labelledby="digest-history-title">
        <div className="panel-heading"><div><span className="eyebrow">Delivery operations</span><h2 id="digest-history-title">Ιστορικό digests και webhooks</h2></div><Badge>{digests.length + deliveries.length} runs</Badge></div>
        <div className="alert-history-grid">
          <div className="compact-list">{digests.slice(0, 8).map((digest) => <div className="compact-row" key={digest.id}><BellRing size={15} /><span>{digest.schedule.replaceAll("_", " ")}<small>{digest.event_count} events · {new Intl.DateTimeFormat("el-GR", { dateStyle: "short", timeStyle: "short" }).format(new Date(digest.created_at))}</small></span><Badge tone={digest.status === "DELIVERED" ? "green" : "amber"}>{digest.status}</Badge></div>)}{!digests.length && <EmptyState title="Δεν έχει εκτελεστεί digest" />}</div>
          <div className="compact-list">{deliveries.slice(0, 8).map((delivery) => <div className="compact-row" key={delivery.id}><Webhook size={15} /><span>{delivery.channel}<small>{delivery.attempt_count} attempt(s) · HTTP {delivery.response_status ?? "-"}</small></span><Badge tone={delivery.status === "DELIVERED" ? "green" : "amber"}>{delivery.status}</Badge></div>)}{!deliveries.length && <EmptyState title="Δεν υπάρχουν webhook deliveries" />}</div>
        </div>
      </section>
    </div>
  );
}

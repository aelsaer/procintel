"use client";

import { useEffect, useState } from "react";
import { Bell, CheckCircle2, ClipboardCheck, FileBadge, Loader2, MessageSquare, Plus, RefreshCw, Send, Sparkles, Trash2 } from "lucide-react";
import {
  ApiError,
  apiFetch,
  type AccountMember,
  type BidRequirement,
  type BidTask,
  type BidWorkspace,
  type TenantCertificate,
} from "@/lib/api";
import { Badge, EmptyState } from "@/components/procurement-ui";
import { ProposalProduction } from "@/components/proposal-production";

const workspaceStatuses: BidWorkspace["status"][] = [
  "QUALIFYING",
  "PREPARING",
  "REVIEW",
  "SUBMITTED",
  "WON",
  "LOST",
  "ARCHIVED",
];
const decisions: BidWorkspace["decision"][] = ["PENDING", "BID", "CONDITIONAL", "NO_BID"];
const taskStatuses: BidTask["status"][] = ["TODO", "IN_PROGRESS", "BLOCKED", "DONE"];
const requirementStatuses: BidRequirement["status"][] = ["UNREVIEWED", "MET", "PARTIAL", "MISSING", "NOT_APPLICABLE"];
const requirementTypes: BidRequirement["requirement_type"][] = [
  "ELIGIBILITY",
  "TECHNICAL",
  "FINANCIAL",
  "CERTIFICATE",
  "DELIVERABLE",
  "DEADLINE",
  "LEGAL",
  "OTHER",
];

function localDateTime(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  const offset = date.getTimezoneOffset();
  return new Date(date.getTime() - offset * 60_000).toISOString().slice(0, 16);
}

export function BidWorkspacePanel({ processId }: { processId: string }) {
  const [workspace, setWorkspace] = useState<BidWorkspace | null>(null);
  const [available, setAvailable] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [taskTitle, setTaskTitle] = useState("");
  const [requirementTitle, setRequirementTitle] = useState("");
  const [requirementType, setRequirementType] = useState<BidRequirement["requirement_type"]>("TECHNICAL");
  const [members, setMembers] = useState<AccountMember[]>([]);
  const [certificateLibrary, setCertificateLibrary] = useState<TenantCertificate[]>([]);
  const [comment, setComment] = useState("");
  const [remindAt, setRemindAt] = useState("");
  const [reminderUserId, setReminderUserId] = useState("");
  const [certificateTitle, setCertificateTitle] = useState("");
  const [certificateType, setCertificateType] = useState("ISO");
  const [certificateRequirementId, setCertificateRequirementId] = useState("");
  const [crmProvider, setCrmProvider] = useState("HUBSPOT");

  useEffect(() => {
    let active = true;
    void Promise.all([
      apiFetch<AccountMember[]>("/v1/account/members"),
      apiFetch<TenantCertificate[]>("/v1/bids/certificates"),
    ]).then(([nextMembers, nextCertificates]) => {
      if (!active) return;
      setMembers(nextMembers);
      setCertificateLibrary(nextCertificates);
    }).catch(() => undefined);
    void apiFetch<BidWorkspace>(`/v1/bids/${processId}`)
      .then((value) => {
        if (!active) return;
        setWorkspace(value);
        setAvailable(true);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        if (reason instanceof ApiError && reason.status === 404) {
          setAvailable(false);
          return;
        }
        setError(reason instanceof Error ? reason.message : "Δεν φορτώθηκε το bid workspace.");
      });
    return () => {
      active = false;
    };
  }, [processId]);

  async function reloadWorkspace() {
    return apiFetch<BidWorkspace>(`/v1/bids/${processId}`);
  }

  async function mutate<T>(request: () => Promise<T>, after: (value: T) => void) {
    setBusy(true);
    setError(null);
    try {
      after(await request());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Η αλλαγή δεν αποθηκεύτηκε.");
    } finally {
      setBusy(false);
    }
  }

  function updateWorkspace(values: Partial<Pick<BidWorkspace, "status" | "decision" | "decision_rationale" | "submission_due_at">>) {
    void mutate(
      () =>
        apiFetch<BidWorkspace>(`/v1/bids/${processId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(values),
        }),
      setWorkspace,
    );
  }

  function updateTask(taskId: string, values: Partial<BidTask>) {
    void mutate(
      () =>
        apiFetch<BidTask>(`/v1/bids/${processId}/tasks/${taskId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(values),
        }),
      (updated) =>
        setWorkspace((current) =>
          current ? { ...current, tasks: current.tasks.map((task) => (task.id === updated.id ? updated : task)) } : current,
        ),
    );
  }

  function updateRequirement(requirementId: string, values: Partial<BidRequirement>) {
    void mutate(
      () =>
        apiFetch<BidRequirement>(`/v1/bids/${processId}/requirements/${requirementId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(values),
        }),
      (updated) =>
        setWorkspace((current) =>
          current
            ? {
                ...current,
                requirements: current.requirements.map((requirement) =>
                  requirement.id === updated.id ? updated : requirement,
                ),
              }
            : current,
        ),
    );
  }

  if (available === null && !error) {
    return <div className="workspace-loading"><Loader2 className="spin" size={18} /> Φόρτωση bid workspace</div>;
  }

  if (available === false) {
    return (
      <div className="bid-empty">
        <ClipboardCheck size={30} aria-hidden="true" />
        <div>
          <h3>Αξιολόγηση και προετοιμασία προσφοράς</h3>
          <p>Καταγράψτε την απόφαση, τις προϋποθέσεις, τα καθήκοντα και τις προθεσμίες της ομάδας.</p>
        </div>
        <button
          className="button button-primary"
          type="button"
          disabled={busy}
          onClick={() =>
            void mutate(
              () => apiFetch<BidWorkspace>(`/v1/bids/${processId}`, { method: "POST" }),
              (created) => {
                setWorkspace(created);
                setAvailable(true);
              },
            )
          }
        >
          <Plus size={16} /> Έναρξη αξιολόγησης
        </button>
        {error && <p className="form-error" role="alert">{error}</p>}
      </div>
    );
  }

  if (!workspace) {
    return <EmptyState title={error ?? "Δεν φορτώθηκε το bid workspace"} />;
  }

  return (
    <div className="bid-workspace" aria-busy={busy}>
      <div className="bid-command-bar">
        <label>
          Στάδιο
          <select value={workspace.status} onChange={(event) => updateWorkspace({ status: event.target.value as BidWorkspace["status"] })}>
            {workspaceStatuses.map((status) => <option key={status}>{status}</option>)}
          </select>
        </label>
        <label>
          Προθεσμία υποβολής
          <input
            type="datetime-local"
            value={localDateTime(workspace.submission_due_at)}
            onChange={(event) => updateWorkspace({ submission_due_at: event.target.value ? new Date(event.target.value).toISOString() : null })}
          />
        </label>
        <div className="bid-decision" role="group" aria-label="Απόφαση συμμετοχής">
          <span>Απόφαση</span>
          <div className="segmented-control">
            {decisions.map((decision) => (
              <button
                type="button"
                key={decision}
                className={workspace.decision === decision ? "is-active" : ""}
                onClick={() => updateWorkspace({ decision })}
              >
                {decision.replace("_", " / ")}
              </button>
            ))}
          </div>
        </div>
      </div>

      <label className="bid-rationale">
        Αιτιολόγηση απόφασης
        <textarea
          rows={2}
          defaultValue={workspace.decision_rationale ?? ""}
          placeholder="Κύριοι λόγοι, περιορισμοί και επόμενη ενέργεια"
          onBlur={(event) => updateWorkspace({ decision_rationale: event.target.value || null })}
        />
      </label>
      {error && <p className="form-error" role="alert">{error}</p>}

      <div className="bid-columns">
        <section className="bid-section" aria-labelledby="bid-tasks-title">
          <div className="bid-section-heading">
            <div>
              <span>Εκτέλεση</span>
              <h3 id="bid-tasks-title">Καθήκοντα ομάδας</h3>
            </div>
            <Badge tone="blue">{workspace.tasks.filter((task) => task.status !== "DONE").length} ανοικτά</Badge>
          </div>
          <form
            className="inline-create"
            onSubmit={(event) => {
              event.preventDefault();
              const title = taskTitle.trim();
              if (!title) return;
              void mutate(
                () =>
                  apiFetch<BidTask>(`/v1/bids/${processId}/tasks`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ title }),
                  }),
                (created) => {
                  setWorkspace((current) => (current ? { ...current, tasks: [...current.tasks, created] } : current));
                  setTaskTitle("");
                },
              );
            }}
          >
            <input value={taskTitle} onChange={(event) => setTaskTitle(event.target.value)} placeholder="Νέο καθήκον" aria-label="Νέο καθήκον" />
            <button className="icon-button" type="submit" title="Προσθήκη καθήκοντος" disabled={busy || !taskTitle.trim()}><Plus size={17} /></button>
          </form>
          <div className="bid-scroll-list">
            {workspace.tasks.length === 0 && <EmptyState title="Δεν έχουν προστεθεί καθήκοντα" />}
            {workspace.tasks.map((task) => (
              <div className="bid-list-row bid-task-row" key={task.id}>
                <CheckCircle2 size={17} aria-hidden="true" />
                <span><strong>{task.title}</strong><small>{task.priority}</small></span>
                <select aria-label={`Κατάσταση ${task.title}`} value={task.status} onChange={(event) => updateTask(task.id, { status: event.target.value as BidTask["status"] })}>
                  {taskStatuses.map((status) => <option key={status}>{status}</option>)}
                </select>
                <select
                  aria-label={`Ανάθεση ${task.title}`}
                  value={task.assigned_user_id ?? ""}
                  onChange={(event) => updateTask(task.id, { assigned_user_id: event.target.value || null })}
                >
                  <option value="">Χωρίς ανάθεση</option>
                  {members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name ?? member.email}</option>)}
                </select>
                <input
                  aria-label={`Προθεσμία ${task.title}`}
                  type="datetime-local"
                  value={localDateTime(task.due_at)}
                  onChange={(event) => updateTask(task.id, { due_at: event.target.value ? new Date(event.target.value).toISOString() : null })}
                />
                <button
                  className="icon-button is-danger"
                  type="button"
                  title="Διαγραφή καθήκοντος"
                  onClick={() =>
                    void mutate(
                      () => apiFetch<void>(`/v1/bids/${processId}/tasks/${task.id}`, { method: "DELETE" }),
                      () => setWorkspace((current) => current ? { ...current, tasks: current.tasks.filter((item) => item.id !== task.id) } : current),
                    )
                  }
                ><Trash2 size={15} /></button>
              </div>
            ))}
          </div>
        </section>

        <section className="bid-section" aria-labelledby="bid-requirements-title">
          <div className="bid-section-heading">
            <div>
              <span>Compliance</span>
              <h3 id="bid-requirements-title">Απαιτήσεις</h3>
            </div>
            <div className="bid-heading-actions">
              <Badge tone="amber">{workspace.requirements.filter((item) => item.mandatory && item.status !== "MET").length} εκκρεμείς</Badge>
              <button
                className="icon-button"
                type="button"
                title="Εξαγωγή απαιτήσεων από τα έγγραφα"
                disabled={busy}
                onClick={() =>
                  void mutate(
                    async () => {
                      await apiFetch(`/v1/document-intelligence/${processId}/extract-requirements`, { method: "POST" });
                      return apiFetch<BidWorkspace>(`/v1/bids/${processId}`);
                    },
                    setWorkspace,
                  )
                }
              ><Sparkles size={16} /></button>
            </div>
          </div>
          <form
            className="inline-create inline-create-wide"
            onSubmit={(event) => {
              event.preventDefault();
              const title = requirementTitle.trim();
              if (!title) return;
              void mutate(
                () =>
                  apiFetch<BidRequirement>(`/v1/bids/${processId}/requirements`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ title, requirement_type: requirementType }),
                  }),
                (created) => {
                  setWorkspace((current) => current ? { ...current, requirements: [...current.requirements, created] } : current);
                  setRequirementTitle("");
                },
              );
            }}
          >
            <select aria-label="Τύπος απαίτησης" value={requirementType} onChange={(event) => setRequirementType(event.target.value as BidRequirement["requirement_type"])}>
              {requirementTypes.map((type) => <option key={type}>{type}</option>)}
            </select>
            <input value={requirementTitle} onChange={(event) => setRequirementTitle(event.target.value)} placeholder="Νέα απαίτηση" aria-label="Νέα απαίτηση" />
            <button className="icon-button" type="submit" title="Προσθήκη απαίτησης" disabled={busy || !requirementTitle.trim()}><Plus size={17} /></button>
          </form>
          <div className="bid-scroll-list">
            {workspace.requirements.length === 0 && <EmptyState title="Δεν έχουν καταγραφεί απαιτήσεις" />}
            {workspace.requirements.map((requirement) => (
              <div className="bid-list-row bid-requirement-row" key={requirement.id}>
                <span>
                  <strong>{requirement.title}</strong>
                  <small>{requirement.requirement_type}{requirement.evidence_page ? ` · σελ. ${requirement.evidence_page}` : ""}</small>
                  {requirement.source_excerpt && <q>{requirement.source_excerpt}</q>}
                </span>
                <select aria-label={`Κατάσταση ${requirement.title}`} value={requirement.status} onChange={(event) => updateRequirement(requirement.id, { status: event.target.value as BidRequirement["status"] })}>
                  {requirementStatuses.map((status) => <option key={status}>{status}</option>)}
                </select>
                <select
                  aria-label={`Υπεύθυνος ${requirement.title}`}
                  value={requirement.owner_user_id ?? ""}
                  onChange={(event) => updateRequirement(requirement.id, { owner_user_id: event.target.value || null })}
                >
                  <option value="">Χωρίς υπεύθυνο</option>
                  {members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name ?? member.email}</option>)}
                </select>
                <input
                  aria-label={`Προθεσμία ${requirement.title}`}
                  type="datetime-local"
                  value={localDateTime(requirement.due_at)}
                  onChange={(event) => updateRequirement(requirement.id, { due_at: event.target.value ? new Date(event.target.value).toISOString() : null })}
                />
                <button
                  className="icon-button is-danger"
                  type="button"
                  title="Διαγραφή απαίτησης"
                  onClick={() =>
                    void mutate(
                      () => apiFetch<void>(`/v1/bids/${processId}/requirements/${requirement.id}`, { method: "DELETE" }),
                      () => setWorkspace((current) => current ? { ...current, requirements: current.requirements.filter((item) => item.id !== requirement.id) } : current),
                    )
                  }
                ><Trash2 size={15} /></button>
              </div>
            ))}
          </div>
        </section>
      </div>

      <ProposalProduction processId={processId} />

      <div className="bid-collaboration-grid">
        <section className="bid-section" aria-labelledby="bid-comments-title">
          <div className="bid-section-heading">
            <div><span>Activity</span><h3 id="bid-comments-title">Συζήτηση ομάδας</h3></div>
            <MessageSquare size={17} />
          </div>
          <form
            className="inline-create"
            onSubmit={(event) => {
              event.preventDefault();
              const body = comment.trim();
              if (!body) return;
              void mutate(
                async () => {
                  await apiFetch(`/v1/bids/${processId}/comments`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ body }),
                  });
                  return reloadWorkspace();
                },
                (next) => { setWorkspace(next); setComment(""); },
              );
            }}
          >
            <input value={comment} onChange={(event) => setComment(event.target.value)} placeholder="Σχόλιο προς την ομάδα" aria-label="Νέο σχόλιο" />
            <button className="icon-button" type="submit" title="Αποστολή σχολίου" disabled={!comment.trim() || busy}><Send size={16} /></button>
          </form>
          <div className="bid-scroll-list">
            {workspace.comments.map((item) => (
              <article className="bid-comment" key={item.id}>
                <strong>{item.author_name ?? item.author_email ?? "Μέλος ομάδας"}</strong>
                <p>{item.body}</p>
                <small>{new Intl.DateTimeFormat("el-GR", { dateStyle: "short", timeStyle: "short" }).format(new Date(item.created_at))}</small>
              </article>
            ))}
            {!workspace.comments.length && <EmptyState title="Δεν υπάρχουν σχόλια" />}
          </div>
        </section>

        <section className="bid-section" aria-labelledby="bid-reminders-title">
          <div className="bid-section-heading">
            <div><span>Schedule</span><h3 id="bid-reminders-title">Υπενθυμίσεις</h3></div>
            <Bell size={17} />
          </div>
          <form
            className="inline-create inline-create-wide"
            onSubmit={(event) => {
              event.preventDefault();
              if (!remindAt) return;
              void mutate(
                async () => {
                  await apiFetch(`/v1/bids/${processId}/reminders`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                      remind_at: new Date(remindAt).toISOString(),
                      assigned_user_id: reminderUserId || null,
                      channel: "IN_APP",
                    }),
                  });
                  return reloadWorkspace();
                },
                (next) => { setWorkspace(next); setRemindAt(""); },
              );
            }}
          >
            <input aria-label="Ημερομηνία υπενθύμισης" type="datetime-local" value={remindAt} onChange={(event) => setRemindAt(event.target.value)} />
            <select aria-label="Παραλήπτης υπενθύμισης" value={reminderUserId} onChange={(event) => setReminderUserId(event.target.value)}>
              <option value="">Εμένα</option>
              {members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name ?? member.email}</option>)}
            </select>
            <button className="icon-button" type="submit" title="Προσθήκη υπενθύμισης" disabled={!remindAt || busy}><Plus size={16} /></button>
          </form>
          <div className="bid-scroll-list">
            {workspace.reminders.map((reminder) => (
              <div className="bid-list-row" key={reminder.id}>
                <Bell size={16} />
                <span><strong>{new Intl.DateTimeFormat("el-GR", { dateStyle: "medium", timeStyle: "short" }).format(new Date(reminder.remind_at))}</strong><small>{reminder.channel}{reminder.last_error?.message ? ` · ${reminder.last_error.message}` : ""}</small></span>
                <Badge tone={reminder.status === "FAILED" ? "red" : reminder.status === "PENDING" ? "amber" : reminder.status === "SENT" ? "green" : "neutral"}>{reminder.status}</Badge>
                {reminder.status === "FAILED" ? <button className="icon-button" type="button" title="Επανάληψη αποστολής" disabled={busy} onClick={() => void mutate(
                  async () => {
                    await apiFetch(`/v1/bids/${processId}/reminders/${reminder.id}/retry`, { method: "POST" });
                    return reloadWorkspace();
                  },
                  setWorkspace,
                )}><RefreshCw size={15} /></button> : null}
                <button className="icon-button is-danger" type="button" title="Διαγραφή υπενθύμισης" onClick={() => void mutate(
                  async () => {
                    await apiFetch(`/v1/bids/${processId}/reminders/${reminder.id}`, { method: "DELETE" });
                    return reloadWorkspace();
                  },
                  setWorkspace,
                )}><Trash2 size={15} /></button>
              </div>
            ))}
            {!workspace.reminders.length && <EmptyState title="Δεν υπάρχουν υπενθυμίσεις" />}
          </div>
        </section>

        <section className="bid-section" aria-labelledby="bid-certificates-title">
          <div className="bid-section-heading">
            <div><span>Reusable library</span><h3 id="bid-certificates-title">Πιστοποιητικά</h3></div>
            <FileBadge size={17} />
          </div>
          <form
            className="inline-create inline-create-wide"
            onSubmit={(event) => {
              event.preventDefault();
              if (!certificateTitle.trim()) return;
              void mutate(
                () => apiFetch<TenantCertificate>("/v1/bids/certificates", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ title: certificateTitle.trim(), certificate_type: certificateType }),
                }),
                (created) => {
                  setCertificateLibrary((current) => [...current, created]);
                  setCertificateTitle("");
                },
              );
            }}
          >
            <input value={certificateTitle} onChange={(event) => setCertificateTitle(event.target.value)} placeholder="Τίτλος πιστοποιητικού" aria-label="Τίτλος πιστοποιητικού" />
            <input value={certificateType} onChange={(event) => setCertificateType(event.target.value)} placeholder="Τύπος" aria-label="Τύπος πιστοποιητικού" />
            <button className="icon-button" type="submit" title="Προσθήκη στη βιβλιοθήκη"><Plus size={16} /></button>
          </form>
          <label>
            Σύνδεση με απαίτηση
            <select value={certificateRequirementId} onChange={(event) => setCertificateRequirementId(event.target.value)}>
              <option value="">Γενικό για την προσφορά</option>
              {workspace.requirements.map((requirement) => <option key={requirement.id} value={requirement.id}>{requirement.title}</option>)}
            </select>
          </label>
          <div className="bid-scroll-list">
            {certificateLibrary.map((certificate) => {
              const linked = workspace.certificates.some((item) => item.id === certificate.id && (item.requirement_id ?? "") === certificateRequirementId);
              return (
                <div className="bid-list-row" key={certificate.id}>
                  <FileBadge size={16} />
                  <span><strong>{certificate.title}</strong><small>{certificate.certificate_type}{certificate.expires_at ? ` · λήξη ${certificate.expires_at}` : ""}</small></span>
                  <button className="button button-secondary" type="button" disabled={linked || busy} onClick={() => void mutate(
                    async () => {
                      await apiFetch(`/v1/bids/${processId}/certificates`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ certificate_id: certificate.id, requirement_id: certificateRequirementId || null }),
                      });
                      return reloadWorkspace();
                    },
                    setWorkspace,
                  )}>{linked ? "Συνδεδεμένο" : "Σύνδεση"}</button>
                </div>
              );
            })}
            {!certificateLibrary.length && <EmptyState title="Η βιβλιοθήκη είναι κενή" />}
          </div>
        </section>

        <section className="bid-section" aria-labelledby="bid-crm-title">
          <div className="bid-section-heading">
            <div><span>Sales operations</span><h3 id="bid-crm-title">CRM handoff</h3></div>
            <Send size={17} />
          </div>
          <form
            className="inline-create"
            onSubmit={(event) => {
              event.preventDefault();
              void mutate(
                async () => {
                  await apiFetch(`/v1/bids/${processId}/crm-handoffs`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                      provider: crmProvider,
                      payload: { title: workspace.process_title, stage: workspace.status, decision: workspace.decision },
                    }),
                  });
                  return reloadWorkspace();
                },
                setWorkspace,
              );
            }}
          >
            <select aria-label="CRM provider" value={crmProvider} onChange={(event) => setCrmProvider(event.target.value)}>
              <option value="HUBSPOT">HubSpot</option>
              <option value="SALESFORCE">Salesforce</option>
              <option value="PIPEDRIVE">Pipedrive</option>
              <option value="CUSTOM">Custom webhook</option>
            </select>
            <button className="button button-secondary" type="submit" disabled={busy}><Send size={15} />Αποστολή</button>
          </form>
          <div className="bid-scroll-list">
            {workspace.crm_handoffs.map((handoff) => (
              <div className="bid-list-row" key={handoff.id}>
                <Send size={16} />
                <span><strong>{handoff.provider}</strong><small>{handoff.external_reference ?? handoff.error_message ?? handoff.created_at}</small></span>
                <Badge tone={handoff.status === "SYNCED" ? "green" : handoff.status === "FAILED" ? "red" : "amber"}>{handoff.status}</Badge>
              </div>
            ))}
            {!workspace.crm_handoffs.length && <EmptyState title="Δεν έχει γίνει CRM handoff" />}
          </div>
        </section>
      </div>
    </div>
  );
}

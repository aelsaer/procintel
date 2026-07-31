"use client";

import { FormEvent, useState } from "react";
import { useCustom } from "@refinedev/core";
import { Check, Copy, CreditCard, KeyRound, ShieldCheck, Trash2, UserPlus, Users } from "lucide-react";
import {
  api,
  type AccountApiKey,
  type AccountInvitation,
  type AccountMember,
} from "@/lib/api";
import { BackLink, Badge, EmptyState, ErrorState, LoadingState, PageHeader } from "@/components/procurement-ui";
import { formatDate } from "@/lib/format";
import { CommercialSettings } from "@/components/commercial-settings";

const roles: AccountMember["role"][] = ["OWNER", "ADMIN", "ANALYST", "SALES", "BID_MANAGER", "VIEWER"];

export default function SettingsPage() {
  const [mode, setMode] = useState<"access" | "commercial">("access");
  const membersQuery = useCustom<AccountMember[]>({ url: "/v1/account/members", method: "get", queryOptions: { retry: 1 } });
  const invitationsQuery = useCustom<AccountInvitation[]>({ url: "/v1/account/invitations", method: "get", queryOptions: { retry: 1 } });
  const keysQuery = useCustom<AccountApiKey[]>({ url: "/v1/account/api-keys", method: "get", queryOptions: { retry: 1 } });
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<AccountMember["role"]>("VIEWER");
  const [keyName, setKeyName] = useState("");
  const [oneTimeSecret, setOneTimeSecret] = useState<{ label: string; value: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const members = Array.isArray(membersQuery.result.data) ? membersQuery.result.data : [];
  const invitations = Array.isArray(invitationsQuery.result.data) ? invitationsQuery.result.data : [];
  const keys = Array.isArray(keysQuery.result.data) ? keysQuery.result.data : [];

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Η αλλαγή δεν αποθηκεύτηκε.");
    } finally {
      setBusy(false);
    }
  }

  function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const email = inviteEmail.trim();
    if (!email) return;
    void run(async () => {
      const result = await api.createInvitation(email, inviteRole);
      if (result.invitation_token) {
        setOneTimeSecret({ label: `Invitation για ${result.email}`, value: result.invitation_token });
      }
      setInviteEmail("");
      await invitationsQuery.query.refetch();
    });
  }

  function createKey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = keyName.trim();
    if (!name) return;
    void run(async () => {
      const result = await api.createApiKey(name);
      if (result.key) setOneTimeSecret({ label: result.name, value: result.key });
      setKeyName("");
      await keysQuery.query.refetch();
    });
  }

  return (
    <div className="account-settings">
      <PageHeader
        eyebrow="Workspace administration"
        title="Ομάδα και πρόσβαση"
        subtitle="Διαχειριστείτε μέλη, προσκλήσεις και machine-to-machine πρόσβαση."
        actions={<BackLink />}
      />

      <div className="segmented-control settings-mode" aria-label="Κατηγορία ρυθμίσεων">
        <button type="button" className={mode === "access" ? "is-active" : ""} onClick={() => setMode("access")}><Users size={15} />Ομάδα και πρόσβαση</button>
        <button type="button" className={mode === "commercial" ? "is-active" : ""} onClick={() => setMode("commercial")}><CreditCard size={15} />Πλάνο και υποστήριξη</button>
      </div>

      {mode === "commercial" ? <CommercialSettings /> : <>
      {error && <p className="form-error" role="alert">{error}</p>}
      {oneTimeSecret && (
        <div className="one-time-secret" role="status">
          <ShieldCheck size={20} aria-hidden="true" />
          <div>
            <strong>{oneTimeSecret.label}</strong>
            <small>Εμφανίζεται μόνο τώρα. Αποθηκεύστε το σε password manager.</small>
            <code>{oneTimeSecret.value}</code>
          </div>
          <button
            className="icon-button"
            type="button"
            title="Αντιγραφή"
            onClick={() => void navigator.clipboard.writeText(oneTimeSecret.value).then(() => setCopied(true))}
          >
            {copied ? <Check size={16} /> : <Copy size={16} />}
          </button>
        </div>
      )}

      <section className="settings-band" aria-labelledby="members-title">
        <div className="settings-heading">
          <div><Users size={18} /><span><strong id="members-title">Μέλη workspace</strong><small>{members.length} ενεργά μέλη</small></span></div>
        </div>
        {membersQuery.query.isLoading && <LoadingState label="Φόρτωση μελών" />}
        {membersQuery.query.isError && <ErrorState error={membersQuery.query.error} />}
        <div className="settings-table" role="table" aria-label="Μέλη workspace">
          {members.map((member) => (
            <div className="settings-row" role="row" key={member.id}>
              <span><strong>{member.display_name ?? member.email}</strong><small>{member.email} · από {formatDate(member.joined_at)}</small></span>
              {member.mfa_enabled ? <Badge tone="green">MFA</Badge> : <Badge tone="amber">χωρίς MFA</Badge>}
              <select
                aria-label={`Ρόλος ${member.email}`}
                value={member.role}
                disabled={busy}
                onChange={(event) => void run(async () => {
                  await api.updateMemberRole(member.id, event.target.value as AccountMember["role"]);
                  await membersQuery.query.refetch();
                })}
              >
                {roles.map((role) => <option key={role}>{role}</option>)}
              </select>
            </div>
          ))}
        </div>
      </section>

      <section className="settings-band" aria-labelledby="invitations-title">
        <div className="settings-heading">
          <div><UserPlus size={18} /><span><strong id="invitations-title">Προσκλήσεις</strong><small>Το token λήγει σε 7 ημέρες</small></span></div>
          <form className="settings-create" onSubmit={invite}>
            <input type="email" required value={inviteEmail} onChange={(event) => setInviteEmail(event.target.value)} placeholder="email@company.gr" aria-label="Email πρόσκλησης" />
            <select value={inviteRole} onChange={(event) => setInviteRole(event.target.value as AccountMember["role"])} aria-label="Ρόλος πρόσκλησης">
              {roles.filter((role) => role !== "OWNER").map((role) => <option key={role}>{role}</option>)}
            </select>
            <button className="button button-primary" type="submit" disabled={busy}>Πρόσκληση</button>
          </form>
        </div>
        {invitationsQuery.query.isLoading && <LoadingState label="Φόρτωση προσκλήσεων" />}
        {!invitationsQuery.query.isLoading && invitations.length === 0 && <EmptyState title="Δεν υπάρχουν προσκλήσεις" />}
        <div className="settings-table">
          {invitations.map((invitation) => (
            <div className="settings-row" key={invitation.id}>
              <span><strong>{invitation.email}</strong><small>Λήξη {formatDate(invitation.expires_at)}</small></span>
              <Badge tone={invitation.accepted_at ? "green" : invitation.revoked_at ? "neutral" : "blue"}>
                {invitation.accepted_at ? "ACCEPTED" : invitation.revoked_at ? "REVOKED" : "PENDING"}
              </Badge>
              {!invitation.accepted_at && !invitation.revoked_at && (
                <button className="icon-button is-danger" type="button" title="Ανάκληση πρόσκλησης" onClick={() => void run(async () => {
                  await api.revokeInvitation(invitation.id);
                  await invitationsQuery.query.refetch();
                })}><Trash2 size={15} /></button>
              )}
            </div>
          ))}
        </div>
      </section>

      <section className="settings-band" aria-labelledby="keys-title">
        <div className="settings-heading">
          <div><KeyRound size={18} /><span><strong id="keys-title">API keys</strong><small>Read-only bearer access, hashed at rest</small></span></div>
          <form className="settings-create settings-create-key" onSubmit={createKey}>
            <input required value={keyName} onChange={(event) => setKeyName(event.target.value)} placeholder="π.χ. BI integration" aria-label="Όνομα API key" />
            <button className="button button-primary" type="submit" disabled={busy}>Νέο key</button>
          </form>
        </div>
        {keysQuery.query.isLoading && <LoadingState label="Φόρτωση API keys" />}
        {!keysQuery.query.isLoading && keys.length === 0 && <EmptyState title="Δεν υπάρχουν API keys" />}
        <div className="settings-table">
          {keys.map((key) => (
            <div className="settings-row" key={key.id}>
              <span><strong>{key.name}</strong><small><code>{key.key_prefix}…</code> · χρήση {key.last_used_at ? formatDate(key.last_used_at) : "ποτέ"}</small></span>
              <Badge tone={key.revoked_at ? "neutral" : "green"}>{key.revoked_at ? "REVOKED" : "ACTIVE"}</Badge>
              {!key.revoked_at && <button className="icon-button is-danger" type="button" title="Ανάκληση API key" onClick={() => void run(async () => {
                await api.revokeApiKey(key.id);
                await keysQuery.query.refetch();
              })}><Trash2 size={15} /></button>}
            </div>
          ))}
        </div>
      </section>
      </>}
    </div>
  );
}

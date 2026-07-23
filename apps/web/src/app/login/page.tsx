"use client";

import { useState } from "react";
import { useLogin } from "@refinedev/core";
import { ArrowRight, Building2, ShieldCheck } from "lucide-react";
import { getOidcConfig, startLoginRedirect } from "@/lib/oidc";

export default function LoginPage() {
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const login = useLogin<{ mode: "local" }>();
  const configured = getOidcConfig() !== null;

  async function handleLogin() {
    setStarting(true);
    setError(null);
    try {
      await startLoginRedirect();
    } catch (err) {
      setError((err as Error).message);
      setStarting(false);
    }
  }

  async function handleLocalLogin() {
    setError(null);
    const result = await login.mutateAsync({ mode: "local" });
    if (!result.success) {
      setError(result.error?.message ?? "Δεν ήταν δυνατή η σύνδεση στο τοπικό workspace.");
    }
  }

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <div className="auth-brand"><span className="auth-brand-mark" aria-hidden="true">P</span><span>Procintel<small>Procurement intelligence</small></span></div>
        <div className="auth-heading"><span className="auth-icon" aria-hidden="true"><Building2 size={20} /></span><h1>Σύνδεση στο workspace</h1><p>Ευκαιρίες, ανταγωνισμός και δημόσιες συμβάσεις σε έναν ασφαλή χώρο εργασίας.</p></div>
        {error && (
          <p role="alert" className="auth-error">
            {error}
          </p>
        )}
        {configured ? (
          <button className="button button-primary auth-submit" type="button" onClick={() => void handleLogin()} disabled={starting}>
            <ShieldCheck size={17} aria-hidden="true" />
            {starting ? "Ανακατεύθυνση…" : "Σύνδεση με SSO"}
            <ArrowRight size={17} aria-hidden="true" />
          </button>
        ) : (
          <>
            <button className="button button-primary auth-submit" type="button" onClick={() => void handleLocalLogin()} disabled={login.isPending}>
              <ShieldCheck size={17} aria-hidden="true" />
              {login.isPending ? "Έλεγχος workspace…" : "Είσοδος στο τοπικό workspace"}
              <ArrowRight size={17} aria-hidden="true" />
            </button>
            <p className="auth-environment">Development access · Local owner</p>
          </>
        )}
      </div>
    </div>
  );
}

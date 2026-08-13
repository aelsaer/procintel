"use client";

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useLogin } from "@refinedev/core";
import { CheckCircle2, LoaderCircle, ShieldAlert } from "lucide-react";
import { completeLoginCallback } from "@/lib/oidc";
import { apiFetch } from "@/lib/api";

export default function CallbackPage() {
  const searchParams = useSearchParams();
  const { mutateAsync: login } = useLogin();
  const [error, setError] = useState<string | null>(null);
  const ranOnce = useRef(false);

  useEffect(() => {
    if (ranOnce.current) return;
    ranOnce.current = true;
    (async () => {
      try {
        const result = await completeLoginCallback(searchParams);
        await apiFetch("/v1/commercial/provision", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ organization_name: result.organizationName ?? null }),
        });
        const loginResult = await login({ redirectTo: result.returnTo });
        if (!loginResult.success) {
          throw loginResult.error ?? new Error("Το workspace δεν αποδέχτηκε τη σύνδεση.");
        }
      } catch (err) {
        setError((err as Error).message);
      }
    })();
  }, [searchParams, login]);

  return (
    <div className="auth-screen">
      <div className="auth-callback">
        <div className="auth-brand">
          <span className="auth-brand-mark" aria-hidden="true">P</span>
          <span>Procintel<small>Procurement intelligence</small></span>
        </div>
        {error ? (
          <>
            <span className="auth-callback-icon auth-callback-error" aria-hidden="true"><ShieldAlert size={24} /></span>
            <h1>Αποτυχία σύνδεσης</h1>
            <p role="alert" className="auth-error">
              {error}
            </p>
            <a className="button button-secondary" href="/login">
              Δοκιμάστε ξανά
            </a>
          </>
        ) : (
          <>
            <span className="auth-callback-icon" aria-hidden="true">
              <LoaderCircle className="auth-spinner" size={24} />
            </span>
            <h1>Ολοκλήρωση σύνδεσης</h1>
            <p role="status">Επιβεβαιώνουμε την ταυτότητα και ανοίγουμε το workspace.</p>
            <span className="auth-callback-proof"><CheckCircle2 size={14} aria-hidden="true" /> Ασφαλής ανταλλαγή PKCE</span>
          </>
        )}
      </div>
    </div>
  );
}

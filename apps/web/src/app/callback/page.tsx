"use client";

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useLogin } from "@refinedev/core";
import { completeLoginCallback } from "@/lib/oidc";

export default function CallbackPage() {
  const searchParams = useSearchParams();
  const { mutate: login } = useLogin();
  const [error, setError] = useState<string | null>(null);
  const ranOnce = useRef(false);

  useEffect(() => {
    if (ranOnce.current) return;
    ranOnce.current = true;
    (async () => {
      try {
        const token = await completeLoginCallback(searchParams);
        login({ token });
      } catch (err) {
        setError((err as Error).message);
      }
    })();
  }, [searchParams, login]);

  return (
    <div className="auth-screen">
      <div className="auth-card">
        {error ? (
          <>
            <h1>Αποτυχία σύνδεσης</h1>
            <p role="alert" className="auth-error">
              {error}
            </p>
            <a className="button button-secondary" href="/login">
              Δοκιμάστε ξανά
            </a>
          </>
        ) : (
          <p>Ολοκλήρωση σύνδεσης…</p>
        )}
      </div>
    </div>
  );
}

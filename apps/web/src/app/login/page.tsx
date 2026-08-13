"use client";

import { useEffect, useState } from "react";
import { useLogin } from "@refinedev/core";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  ArrowRight,
  BarChart3,
  Building2,
  Check,
  Database,
  LockKeyhole,
  Radar,
  ShieldCheck,
  UserPlus,
} from "lucide-react";
import { getOidcConfig, startLoginRedirect } from "@/lib/oidc";

export default function LoginPage() {
  const searchParams = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [organizationName, setOrganizationName] = useState("");
  const login = useLogin<{ mode: "local" }>();
  const intent = searchParams.get("view") === "signup" ? "signup" : "signin";
  const returnTo = searchParams.get("to");
  const returnQuery = returnTo ? `&to=${encodeURIComponent(returnTo)}` : "";

  useEffect(() => {
    let active = true;
    void getOidcConfig().then((config) => {
      if (active) setConfigured(config !== null);
    });
    return () => {
      active = false;
    };
  }, []);

  async function handleLogin() {
    if (intent === "signup" && !organizationName.trim()) {
      setError("Συμπληρώστε την επωνυμία της επιχείρησης.");
      return;
    }
    setStarting(true);
    setError(null);
    try {
      startLoginRedirect({ intent, returnTo, organizationName });
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
      <div className="auth-shell">
        <aside className="auth-context" aria-label="Procintel">
          <div className="auth-brand auth-brand-inverse">
            <span className="auth-brand-mark" aria-hidden="true">P</span>
            <span>Procintel<small>Procurement intelligence</small></span>
          </div>

          <div className="auth-context-copy">
            <span className="auth-kicker">ΕΝΙΑΙΟ PROCUREMENT WORKSPACE</span>
            <h2>Από τη δημόσια εγγραφή στην επιχειρηματική απόφαση.</h2>
            <p>Συνδεδεμένα δεδομένα συμβάσεων, αναδόχων, χρηματοδότησης και γεωγραφίας.</p>
          </div>

          <div className="auth-signal-list" aria-label="Πεδία ανάλυσης">
            <span><Radar size={16} aria-hidden="true" /><strong>Opportunity radar</strong><small>Στοχευμένες προκηρύξεις</small></span>
            <span><BarChart3 size={16} aria-hidden="true" /><strong>Market intelligence</strong><small>Αγοραστές και ανταγωνισμός</small></span>
            <span><Database size={16} aria-hidden="true" /><strong>Evidence graph</strong><small>ΚΗΜΔΗΣ · Διαύγεια · TED</small></span>
          </div>

          <div className="auth-trust">
            <ShieldCheck size={16} aria-hidden="true" />
            <span><strong>OIDC protected</strong><small>Authorization Code + PKCE</small></span>
          </div>
        </aside>

        <section className="auth-panel" aria-labelledby="auth-title">
          <div className="auth-mobile-brand">
            <span className="auth-brand-mark" aria-hidden="true">P</span>
            <strong>Procintel</strong>
          </div>

          <nav className="auth-mode-switch" aria-label="Τρόπος πρόσβασης">
            <Link
              className={intent === "signin" ? "active" : ""}
              href={`/login?view=signin${returnQuery}`}
              aria-current={intent === "signin" ? "page" : undefined}
            >
              Σύνδεση
            </Link>
            <Link
              className={intent === "signup" ? "active" : ""}
              href={`/login?view=signup${returnQuery}`}
              aria-current={intent === "signup" ? "page" : undefined}
            >
              Εγγραφή
            </Link>
          </nav>

          <div className="auth-heading">
            <span className="auth-icon" aria-hidden="true">
              {intent === "signup" ? <UserPlus size={20} /> : <Building2 size={20} />}
            </span>
            <span className="auth-overline">{intent === "signup" ? "ΝΕΟΣ ΛΟΓΑΡΙΑΣΜΟΣ" : "ΑΣΦΑΛΗΣ ΠΡΟΣΒΑΣΗ"}</span>
            <h1 id="auth-title">
              {intent === "signup" ? "Δημιουργία λογαριασμού" : "Σύνδεση στο workspace"}
            </h1>
            <p>
              {intent === "signup"
                ? "Δημιουργήστε λογαριασμό και αποκτήστε πρόσβαση στην εικόνα της δημόσιας αγοράς."
                : "Συνεχίστε στην καθημερινή εικόνα ευκαιριών, αγοράς και διαγωνισμών."}
            </p>
          </div>

          {intent === "signup" && (
            <>
              <label className="auth-organization">
                Επωνυμία επιχείρησης
                <input
                  value={organizationName}
                  onChange={(event) => setOrganizationName(event.target.value)}
                  placeholder="π.χ. Example Technologies A.E."
                  maxLength={200}
                  autoComplete="organization"
                />
              </label>
              <ul className="auth-checks">
                <li><Check size={15} aria-hidden="true" /> Πρόσβαση στο Opportunity Radar</li>
                <li><Check size={15} aria-hidden="true" /> Αναζήτηση συμβάσεων και αναδόχων</li>
                <li><Check size={15} aria-hidden="true" /> Analytics αγοράς και γεωγραφίας</li>
              </ul>
            </>
          )}

          {error && <p role="alert" className="auth-error">{error}</p>}

          {configured === null ? (
            <div className="auth-progress" role="status">
              <span className="auth-progress-dot" />
              Έλεγχος ασφαλούς σύνδεσης…
            </div>
          ) : configured ? (
            <button className="button button-primary auth-submit" type="button" onClick={() => void handleLogin()} disabled={starting}>
              {intent === "signup" ? <UserPlus size={17} aria-hidden="true" /> : <LockKeyhole size={17} aria-hidden="true" />}
              {starting
                ? "Ανακατεύθυνση…"
                : intent === "signup"
                  ? "Δημιουργία λογαριασμού"
                  : "Ασφαλής σύνδεση"}
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

          <p className="auth-privacy">
            Τα στοιχεία σύνδεσης καταχωρίζονται μόνο στον identity provider.
          </p>
        </section>
      </div>
    </div>
  );
}

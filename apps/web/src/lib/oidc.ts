/** Browser-facing OIDC helpers. Tokens remain in an encrypted HttpOnly cookie. */

export interface OidcConfig {
  issuerUrl: string;
  clientId: string;
  redirectUri: string;
}

export interface CompletedLogin {
  returnTo: string;
  organizationName?: string;
}

let runtimeConfigPromise: Promise<OidcConfig | null> | null = null;

export async function getOidcConfig(): Promise<OidcConfig | null> {
  if (!runtimeConfigPromise) {
    runtimeConfigPromise = fetch("/runtime-config", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) return null;
        const config = (await response.json()) as Partial<OidcConfig>;
        return config.issuerUrl && config.clientId && config.redirectUri
          ? { issuerUrl: config.issuerUrl, clientId: config.clientId, redirectUri: config.redirectUri }
          : null;
      })
      .catch(() => null);
  }
  return runtimeConfigPromise;
}

export function startLoginRedirect(options?: {
  intent?: "signin" | "signup";
  returnTo?: string | null;
  organizationName?: string;
}): void {
  const url = new URL("/auth/login", window.location.origin);
  url.searchParams.set("intent", options?.intent ?? "signin");
  if (options?.returnTo) url.searchParams.set("returnTo", options.returnTo);
  if (options?.organizationName?.trim()) url.searchParams.set("organizationName", options.organizationName.trim());
  window.location.href = url.toString();
}

export async function completeLoginCallback(searchParams: URLSearchParams): Promise<CompletedLogin> {
  const response = await fetch(`/auth/callback?${searchParams.toString()}`, {
    method: "GET",
    cache: "no-store",
    credentials: "same-origin",
  });
  const body = (await response.json().catch(() => ({}))) as CompletedLogin & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "Δεν ολοκληρώθηκε η σύνδεση.");
  return body;
}

/** Clears storage used by older releases. Current credentials are HttpOnly. */
export function clearOidcSession(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem("procintel_oidc_session");
  window.localStorage.removeItem("procintel_access_token");
  window.sessionStorage.removeItem("procintel_oidc_pkce");
}

/** API calls now authenticate through the same-origin BFF proxy. */
export async function getValidAccessToken(): Promise<null> {
  return null;
}

export function startLogoutRedirect(): boolean {
  if (typeof window === "undefined") return false;
  clearOidcSession();
  window.location.assign("/auth/logout");
  return true;
}

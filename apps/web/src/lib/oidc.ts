/** OIDC Authorization Code + PKCE session handling for the public web client. */

const PKCE_SESSION_KEY = "procintel_oidc_pkce";
const OIDC_SESSION_KEY = "procintel_oidc_session";
const ACCESS_TOKEN_KEY = "procintel_access_token";
const REFRESH_SKEW_MS = 45_000;

export interface OidcConfig {
  issuerUrl: string;
  clientId: string;
  redirectUri: string;
}

let runtimeConfigPromise: Promise<OidcConfig | null> | null = null;

export async function getOidcConfig(): Promise<OidcConfig | null> {
  const issuerUrl = process.env.NEXT_PUBLIC_OIDC_ISSUER_URL;
  const clientId = process.env.NEXT_PUBLIC_OIDC_CLIENT_ID;
  const redirectUri = process.env.NEXT_PUBLIC_OIDC_REDIRECT_URI;
  if (issuerUrl && clientId && redirectUri) return { issuerUrl, clientId, redirectUri };
  if (!runtimeConfigPromise) {
    runtimeConfigPromise = fetch("/runtime-config", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) return null;
        const config = (await response.json()) as Partial<OidcConfig>;
        return config.issuerUrl && config.clientId && config.redirectUri
          ? {
              issuerUrl: config.issuerUrl,
              clientId: config.clientId,
              redirectUri: config.redirectUri,
            }
          : null;
      })
      .catch(() => null);
  }
  return runtimeConfigPromise;
}

interface OidcDiscoveryDocument {
  issuer: string;
  authorization_endpoint: string;
  token_endpoint: string;
  end_session_endpoint?: string;
}

async function discover(issuerUrl: string): Promise<OidcDiscoveryDocument> {
  const response = await fetch(`${issuerUrl.replace(/\/$/, "")}/.well-known/openid-configuration`);
  if (!response.ok) throw new Error(`Αποτυχία εύρεσης ρυθμίσεων OIDC (${response.status}).`);
  const document = (await response.json()) as OidcDiscoveryDocument;
  if (document.issuer.replace(/\/$/, "") !== issuerUrl.replace(/\/$/, "")) {
    throw new Error("Ο OIDC provider επέστρεψε διαφορετικό issuer από τον αναμενόμενο.");
  }
  return document;
}

function base64UrlEncode(bytes: ArrayBuffer): string {
  let binary = "";
  for (const byte of new Uint8Array(bytes)) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomUrlSafeString(byteLength: number): string {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  return base64UrlEncode(bytes.buffer);
}

async function sha256(value: string): Promise<ArrayBuffer> {
  return crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
}

interface StoredPkceState {
  codeVerifier: string;
  state: string;
  nonce: string;
  tokenEndpoint: string;
  endSessionEndpoint?: string;
  returnTo: string;
  organizationName?: string;
}

interface StoredOidcSession {
  accessToken: string;
  refreshToken?: string;
  idToken?: string;
  expiresAt: number;
  tokenEndpoint: string;
  endSessionEndpoint?: string;
  clientId: string;
}

interface TokenResponse {
  access_token?: string;
  refresh_token?: string;
  id_token?: string;
  expires_in?: number;
  error?: string;
  error_description?: string;
}

export interface CompletedLogin {
  accessToken: string;
  returnTo: string;
  organizationName?: string;
}

function safeReturnTo(value: string | null | undefined): string {
  return value?.startsWith("/") && !value.startsWith("//") ? value : "/";
}

function readOidcSession(): StoredOidcSession | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(OIDC_SESSION_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as StoredOidcSession;
  } catch {
    clearOidcSession();
    return null;
  }
}

function writeOidcSession(session: StoredOidcSession): void {
  window.localStorage.setItem(OIDC_SESSION_KEY, JSON.stringify(session));
  window.localStorage.setItem(ACCESS_TOKEN_KEY, session.accessToken);
}

export function clearOidcSession(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(OIDC_SESSION_KEY);
  window.localStorage.removeItem(ACCESS_TOKEN_KEY);
  window.sessionStorage.removeItem(PKCE_SESSION_KEY);
}

function readJwtPayload(token: string): Record<string, unknown> {
  const payload = token.split(".")[1];
  if (!payload) return {};
  try {
    return JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/"))) as Record<string, unknown>;
  } catch {
    return {};
  }
}

async function parseTokenResponse(response: Response): Promise<TokenResponse> {
  const body = (await response.json().catch(() => ({}))) as TokenResponse;
  if (!response.ok) {
    throw new Error(body.error_description ?? body.error ?? `OIDC token endpoint: ${response.status}`);
  }
  if (!body.access_token) {
    throw new Error("Η απάντηση του OIDC provider δεν περιείχε access_token.");
  }
  return body;
}

/** Redirects to sign-in or self-registration at the configured identity provider. */
export async function startLoginRedirect(options?: {
  intent?: "signin" | "signup";
  returnTo?: string | null;
  organizationName?: string;
}): Promise<void> {
  const config = await getOidcConfig();
  if (!config) {
    throw new Error(
      "Η σύνδεση OIDC δεν έχει ρυθμιστεί σε αυτό το περιβάλλον."
    );
  }
  const discovery = await discover(config.issuerUrl);
  const codeVerifier = randomUrlSafeString(64);
  const codeChallenge = base64UrlEncode(await sha256(codeVerifier));
  const state = randomUrlSafeString(24);
  const nonce = randomUrlSafeString(24);

  const stored: StoredPkceState = {
    codeVerifier,
    state,
    nonce,
    tokenEndpoint: discovery.token_endpoint,
    endSessionEndpoint: discovery.end_session_endpoint,
    returnTo: safeReturnTo(options?.returnTo),
    organizationName: options?.organizationName?.trim() || undefined,
  };
  window.sessionStorage.setItem(PKCE_SESSION_KEY, JSON.stringify(stored));

  const authorizeUrl = new URL(discovery.authorization_endpoint);
  authorizeUrl.searchParams.set("response_type", "code");
  authorizeUrl.searchParams.set("client_id", config.clientId);
  authorizeUrl.searchParams.set("redirect_uri", config.redirectUri);
  authorizeUrl.searchParams.set("scope", "openid profile email");
  authorizeUrl.searchParams.set("state", state);
  authorizeUrl.searchParams.set("nonce", nonce);
  authorizeUrl.searchParams.set("code_challenge", codeChallenge);
  authorizeUrl.searchParams.set("code_challenge_method", "S256");
  authorizeUrl.searchParams.set("ui_locales", "el");
  if (options?.intent === "signup") authorizeUrl.searchParams.set("prompt", "create");
  window.location.assign(authorizeUrl.toString());
}

/** Exchanges the authorization-code callback's query params for an access token. */
export async function completeLoginCallback(searchParams: URLSearchParams): Promise<CompletedLogin> {
  const oidcError = searchParams.get("error");
  if (oidcError) throw new Error(searchParams.get("error_description") ?? oidcError);

  const config = await getOidcConfig();
  if (!config) throw new Error("Η σύνδεση OIDC δεν έχει ρυθμιστεί σε αυτό το περιβάλλον.");

  const code = searchParams.get("code");
  const returnedState = searchParams.get("state");
  if (!code || !returnedState) throw new Error("Λείπουν οι παράμετροι code/state από την ανακατεύθυνση του OIDC.");

  const raw = window.sessionStorage.getItem(PKCE_SESSION_KEY);
  if (!raw) throw new Error("Δεν βρέθηκε ενεργή διαδικασία σύνδεσης (λείπει το PKCE state) — ξεκινήστε ξανά από /login.");
  window.sessionStorage.removeItem(PKCE_SESSION_KEY);

  const stored = JSON.parse(raw) as StoredPkceState;
  const { codeVerifier, state, nonce, tokenEndpoint } = stored;
  if (returnedState !== state) throw new Error("Μη έγκυρη κατάσταση σύνδεσης (state mismatch) — πιθανή απόπειρα CSRF.");

  const response = await fetch(tokenEndpoint, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code,
      redirect_uri: config.redirectUri,
      client_id: config.clientId,
      code_verifier: codeVerifier,
    }),
  });
  const body = await parseTokenResponse(response);
  if (body.id_token && readJwtPayload(body.id_token).nonce !== nonce) {
    throw new Error("Το OIDC id_token δεν αντιστοιχεί στην ενεργή διαδικασία σύνδεσης.");
  }

  writeOidcSession({
    accessToken: body.access_token!,
    refreshToken: body.refresh_token,
    idToken: body.id_token,
    expiresAt: Date.now() + Math.max(body.expires_in ?? 300, 30) * 1000,
    tokenEndpoint,
    endSessionEndpoint: stored.endSessionEndpoint,
    clientId: config.clientId,
  });
  return {
    accessToken: body.access_token!,
    returnTo: stored.returnTo,
    organizationName: stored.organizationName,
  };
}

let refreshPromise: Promise<string | null> | null = null;

/** Returns a current access token and refreshes it before expiry when possible. */
export async function getValidAccessToken(): Promise<string | null> {
  if (typeof window === "undefined") return null;
  const session = readOidcSession();
  if (!session) return window.localStorage.getItem(ACCESS_TOKEN_KEY);
  if (session.expiresAt - Date.now() > REFRESH_SKEW_MS) return session.accessToken;
  if (!session.refreshToken) {
    clearOidcSession();
    return null;
  }
  if (refreshPromise) return refreshPromise;

  refreshPromise = (async () => {
    try {
      const response = await fetch(session.tokenEndpoint, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "refresh_token",
          refresh_token: session.refreshToken!,
          client_id: session.clientId,
        }),
      });
      const body = await parseTokenResponse(response);
      const refreshed: StoredOidcSession = {
        ...session,
        accessToken: body.access_token!,
        refreshToken: body.refresh_token ?? session.refreshToken,
        idToken: body.id_token ?? session.idToken,
        expiresAt: Date.now() + Math.max(body.expires_in ?? 300, 30) * 1000,
      };
      writeOidcSession(refreshed);
      return refreshed.accessToken;
    } catch {
      clearOidcSession();
      return null;
    } finally {
      refreshPromise = null;
    }
  })();
  return refreshPromise;
}

/** Clears the browser session and starts RP-initiated single logout when available. */
export function startLogoutRedirect(): boolean {
  if (typeof window === "undefined") return false;
  const session = readOidcSession();
  clearOidcSession();
  if (!session?.endSessionEndpoint) return false;

  const logoutUrl = new URL(session.endSessionEndpoint);
  if (session.idToken) logoutUrl.searchParams.set("id_token_hint", session.idToken);
  logoutUrl.searchParams.set("client_id", session.clientId);
  logoutUrl.searchParams.set("post_logout_redirect_uri", `${window.location.origin}/login`);
  window.location.assign(logoutUrl.toString());
  return true;
}

/**
 * OIDC Authorization Code + PKCE (RFC 7636) flow for a public SPA client —
 * no client secret, no server-side session; the access token the token
 * endpoint returns is handed straight to `procurementAuthProvider.login()`,
 * which stores it the same way the previous manual-token bootstrap did
 * (`localStorage["procintel_access_token"]`). Provider-agnostic: no
 * specific IdP is deployed yet, so the issuer's own
 * `.well-known/openid-configuration` document is discovered at login time
 * rather than hardcoding `/authorize`/`/token` paths.
 */

const PKCE_SESSION_KEY = "procintel_oidc_pkce";

export interface OidcConfig {
  issuerUrl: string;
  clientId: string;
  redirectUri: string;
}

export function getOidcConfig(): OidcConfig | null {
  const issuerUrl = process.env.NEXT_PUBLIC_OIDC_ISSUER_URL;
  const clientId = process.env.NEXT_PUBLIC_OIDC_CLIENT_ID;
  const redirectUri = process.env.NEXT_PUBLIC_OIDC_REDIRECT_URI;
  if (!issuerUrl || !clientId || !redirectUri) return null;
  return { issuerUrl, clientId, redirectUri };
}

interface OidcDiscoveryDocument {
  authorization_endpoint: string;
  token_endpoint: string;
}

async function discover(issuerUrl: string): Promise<OidcDiscoveryDocument> {
  const response = await fetch(`${issuerUrl.replace(/\/$/, "")}/.well-known/openid-configuration`);
  if (!response.ok) throw new Error(`Αποτυχία εύρεσης ρυθμίσεων OIDC (${response.status}).`);
  return (await response.json()) as OidcDiscoveryDocument;
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
  tokenEndpoint: string;
}

/** Redirects the browser to the IdP's authorization endpoint. Never returns normally. */
export async function startLoginRedirect(): Promise<void> {
  const config = getOidcConfig();
  if (!config) {
    throw new Error(
      "Η σύνδεση OIDC δεν έχει ρυθμιστεί σε αυτό το περιβάλλον (NEXT_PUBLIC_OIDC_ISSUER_URL / " +
        "NEXT_PUBLIC_OIDC_CLIENT_ID / NEXT_PUBLIC_OIDC_REDIRECT_URI)."
    );
  }
  const discovery = await discover(config.issuerUrl);
  const codeVerifier = randomUrlSafeString(64);
  const codeChallenge = base64UrlEncode(await sha256(codeVerifier));
  const state = randomUrlSafeString(24);

  const stored: StoredPkceState = { codeVerifier, state, tokenEndpoint: discovery.token_endpoint };
  window.sessionStorage.setItem(PKCE_SESSION_KEY, JSON.stringify(stored));

  const authorizeUrl = new URL(discovery.authorization_endpoint);
  authorizeUrl.searchParams.set("response_type", "code");
  authorizeUrl.searchParams.set("client_id", config.clientId);
  authorizeUrl.searchParams.set("redirect_uri", config.redirectUri);
  authorizeUrl.searchParams.set("scope", "openid profile email");
  authorizeUrl.searchParams.set("state", state);
  authorizeUrl.searchParams.set("code_challenge", codeChallenge);
  authorizeUrl.searchParams.set("code_challenge_method", "S256");
  window.location.assign(authorizeUrl.toString());
}

/** Exchanges the authorization-code callback's query params for an access token. */
export async function completeLoginCallback(searchParams: URLSearchParams): Promise<string> {
  // The IdP's own error redirect is independent of our local config and
  // more useful to show than a "not configured" message, so it's checked
  // first even though everything below it needs `config`.
  const oidcError = searchParams.get("error");
  if (oidcError) throw new Error(searchParams.get("error_description") ?? oidcError);

  const config = getOidcConfig();
  if (!config) throw new Error("Η σύνδεση OIDC δεν έχει ρυθμιστεί σε αυτό το περιβάλλον.");

  const code = searchParams.get("code");
  const returnedState = searchParams.get("state");
  if (!code || !returnedState) throw new Error("Λείπουν οι παράμετροι code/state από την ανακατεύθυνση του OIDC.");

  const raw = window.sessionStorage.getItem(PKCE_SESSION_KEY);
  if (!raw) throw new Error("Δεν βρέθηκε ενεργή διαδικασία σύνδεσης (λείπει το PKCE state) — ξεκινήστε ξανά από /login.");
  window.sessionStorage.removeItem(PKCE_SESSION_KEY);

  const { codeVerifier, state, tokenEndpoint } = JSON.parse(raw) as StoredPkceState;
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
  if (!response.ok) throw new Error(`Αποτυχία ανταλλαγής κωδικού για access token (${response.status}).`);

  const body = (await response.json()) as { access_token?: string };
  if (!body.access_token) throw new Error("Η απάντηση του OIDC provider δεν περιείχε access_token.");
  return body.access_token;
}

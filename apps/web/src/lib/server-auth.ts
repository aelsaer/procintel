import "server-only";

import { createCipheriv, createDecipheriv, createHash, randomBytes } from "node:crypto";

const productionCookies = process.env.NODE_ENV === "production";
export const AUTH_SESSION_COOKIE = productionCookies ? "__Host-procintel_session" : "procintel_session";
export const AUTH_PKCE_COOKIE = productionCookies ? "__Host-procintel_pkce" : "procintel_pkce";

export interface BrowserSession {
  accessToken: string;
  refreshToken?: string;
  expiresAt: number;
  tokenEndpoint: string;
  endSessionEndpoint?: string;
  clientId: string;
}

export interface PkceSession {
  codeVerifier: string;
  state: string;
  nonce: string;
  redirectUri: string;
  tokenEndpoint: string;
  endSessionEndpoint?: string;
  clientId: string;
  returnTo: string;
  organizationName?: string;
}

interface OidcDiscoveryDocument {
  issuer: string;
  authorization_endpoint: string;
  token_endpoint: string;
  end_session_endpoint?: string;
}

function encryptionKey(): Buffer {
  const configured = process.env.AUTH_SESSION_SECRET;
  if (!configured && process.env.NODE_ENV === "production") {
    throw new Error("AUTH_SESSION_SECRET is required in production");
  }
  if (configured && Buffer.byteLength(configured, "utf8") < 32) {
    throw new Error("AUTH_SESSION_SECRET must contain at least 32 bytes");
  }
  return createHash("sha256")
    .update(configured ?? "procintel-development-only-session-secret")
    .digest();
}

export function sealSession(value: object): string {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", encryptionKey(), iv);
  const ciphertext = Buffer.concat([cipher.update(JSON.stringify(value), "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();
  return Buffer.concat([iv, tag, ciphertext]).toString("base64url");
}

export function openSession<T>(sealed: string | undefined): T | null {
  if (!sealed) return null;
  try {
    const payload = Buffer.from(sealed, "base64url");
    if (payload.length < 29) return null;
    const decipher = createDecipheriv("aes-256-gcm", encryptionKey(), payload.subarray(0, 12));
    decipher.setAuthTag(payload.subarray(12, 28));
    const plaintext = Buffer.concat([decipher.update(payload.subarray(28)), decipher.final()]);
    return JSON.parse(plaintext.toString("utf8")) as T;
  } catch {
    return null;
  }
}

export const secureCookieOptions = {
  httpOnly: true,
  secure: productionCookies,
  sameSite: "lax" as const,
  path: "/",
};

export function publicOidcConfig(requestUrl: string) {
  const issuerUrl = process.env.OIDC_ISSUER_URL ?? process.env.NEXT_PUBLIC_OIDC_ISSUER_URL;
  const clientId = process.env.OIDC_CLIENT_ID ?? process.env.NEXT_PUBLIC_OIDC_CLIENT_ID;
  const configuredRedirect = process.env.OIDC_REDIRECT_URI ?? process.env.NEXT_PUBLIC_OIDC_REDIRECT_URI;
  if (!issuerUrl || !clientId) return null;

  const requestOrigin = new URL(requestUrl).origin;
  const configured = new URL(configuredRedirect ?? `${requestOrigin}/callback`, requestOrigin);
  const current = new URL(requestOrigin);
  const loopback = (hostname: string) => ["localhost", "127.0.0.1", "[::1]"].includes(hostname);
  if (configured.origin !== current.origin && loopback(configured.hostname) && loopback(current.hostname)) {
    configured.protocol = current.protocol;
    configured.hostname = current.hostname;
    configured.port = current.port;
  }
  return { issuerUrl: issuerUrl.replace(/\/$/, ""), clientId, redirectUri: configured.toString() };
}

export async function discoverOidc(requestUrl: string): Promise<{
  config: NonNullable<ReturnType<typeof publicOidcConfig>>;
  discovery: OidcDiscoveryDocument;
  tokenEndpoint: string;
}> {
  const config = publicOidcConfig(requestUrl);
  if (!config) throw new Error("OIDC is not configured");
  const configuredInternalIssuer = process.env.OIDC_INTERNAL_ISSUER_URL?.trim();
  const internalIssuer = (configuredInternalIssuer || config.issuerUrl).replace(/\/$/, "");
  const response = await fetch(`${internalIssuer}/.well-known/openid-configuration`, {
    cache: "no-store",
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error(`OIDC discovery failed (${response.status})`);
  const discovery = (await response.json()) as OidcDiscoveryDocument;
  if (discovery.issuer.replace(/\/$/, "") !== config.issuerUrl) {
    throw new Error("OIDC discovery returned an unexpected issuer");
  }
  const tokenEndpoint = configuredInternalIssuer
    ? discovery.token_endpoint.replace(config.issuerUrl, internalIssuer)
    : discovery.token_endpoint;
  return { config, discovery, tokenEndpoint };
}

export function safeReturnTo(value: string | null | undefined): string {
  return value?.startsWith("/") && !value.startsWith("//") ? value : "/";
}

export function readJwtPayload(token: string): Record<string, unknown> {
  try {
    const payload = token.split(".")[1];
    return payload ? JSON.parse(Buffer.from(payload, "base64url").toString("utf8")) as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

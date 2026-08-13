import { NextRequest, NextResponse } from "next/server";
import { isIP } from "node:net";

import {
  AUTH_SESSION_COOKIE,
  openSession,
  sealSession,
  secureCookieOptions,
  type BrowserSession,
} from "@/lib/server-auth";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "content-length",
  "content-encoding",
  "host",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

async function refreshSession(session: BrowserSession): Promise<BrowserSession | null> {
  if (session.expiresAt - Date.now() > 45_000) return session;
  if (!session.refreshToken) return null;
  try {
    const clientSecret = process.env.OIDC_CLIENT_SECRET;
    if (process.env.NODE_ENV === "production" && !clientSecret) return null;
    const response = await fetch(session.tokenEndpoint, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "refresh_token",
        refresh_token: session.refreshToken,
        client_id: session.clientId,
        ...(clientSecret ? { client_secret: clientSecret } : {}),
      }),
      cache: "no-store",
      signal: AbortSignal.timeout(10_000),
    });
    const body = await response.json() as {
      access_token?: string;
      refresh_token?: string;
      expires_in?: number;
    };
    if (!response.ok || !body.access_token) return null;
    return {
      ...session,
      accessToken: body.access_token,
      refreshToken: body.refresh_token ?? session.refreshToken,
      expiresAt: Date.now() + Math.max(body.expires_in ?? 300, 30) * 1000,
    };
  } catch {
    return null;
  }
}

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  if (MUTATING_METHODS.has(request.method)) {
    const origin = request.headers.get("origin");
    const fetchSite = request.headers.get("sec-fetch-site");
    if ((origin && origin !== request.nextUrl.origin) || fetchSite === "cross-site") {
      return NextResponse.json({ detail: "Cross-site request rejected" }, { status: 403 });
    }
  }

  const originalSession = openSession<BrowserSession>(request.cookies.get(AUTH_SESSION_COOKIE)?.value);
  const session = originalSession ? await refreshSession(originalSession) : null;
  const { path } = await context.params;
  if (path.length === 1 && path[0] === "metrics") {
    return NextResponse.json({ detail: "Not found" }, { status: 404 });
  }
  const backendBase = (process.env.API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");
  const target = `${backendBase}/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  const headers = new Headers(request.headers);
  for (const name of HOP_BY_HOP_HEADERS) headers.delete(name);
  headers.delete("cookie");
  headers.delete("authorization");
  headers.delete("forwarded");
  headers.delete("x-forwarded-for");
  headers.delete("x-forwarded-host");
  headers.delete("x-forwarded-proto");
  headers.delete("x-procintel-client-ip");
  const forwardedClient = request.headers
    .get("x-forwarded-for")
    ?.split(",")
    .at(-1)
    ?.trim();
  if (forwardedClient && isIP(forwardedClient)) {
    headers.set("x-procintel-client-ip", forwardedClient);
  }
  if (session) headers.set("Authorization", `Bearer ${session.accessToken}`);

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
      redirect: "manual",
      duplex: "half",
    } as RequestInit & { duplex: "half" });
  } catch {
    return NextResponse.json({ detail: "Το API δεν είναι διαθέσιμο." }, { status: 502 });
  }

  const responseHeaders = new Headers(upstream.headers);
  for (const name of HOP_BY_HOP_HEADERS) responseHeaders.delete(name);
  responseHeaders.delete("set-cookie");
  const response = new NextResponse(upstream.body, { status: upstream.status, headers: responseHeaders });
  response.headers.set("Cache-Control", response.headers.get("Cache-Control") ?? "no-store");
  if (originalSession && !session) {
    response.cookies.set(AUTH_SESSION_COOKIE, "", { ...secureCookieOptions, maxAge: 0 });
  } else if (session && session !== originalSession) {
    const sealed = sealSession(session);
    if (sealed.length > 3800) {
      response.cookies.set(AUTH_SESSION_COOKIE, "", { ...secureCookieOptions, maxAge: 0 });
    } else {
      response.cookies.set(AUTH_SESSION_COOKIE, sealed, {
        ...secureCookieOptions,
        maxAge: 12 * 60 * 60,
      });
    }
  }
  return response;
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const HEAD = proxy;

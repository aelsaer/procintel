import { NextRequest, NextResponse } from "next/server";

import {
  AUTH_PKCE_COOKIE,
  AUTH_SESSION_COOKIE,
  openSession,
  secureCookieOptions,
  type BrowserSession,
} from "@/lib/server-auth";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: NextRequest) {
  const session = openSession<BrowserSession>(request.cookies.get(AUTH_SESSION_COOKIE)?.value);
  const loginUrl = new URL("/login", request.nextUrl.origin);
  let destination = loginUrl;
  if (session?.endSessionEndpoint) {
    destination = new URL(session.endSessionEndpoint);
    destination.searchParams.set("client_id", session.clientId);
    destination.searchParams.set("post_logout_redirect_uri", loginUrl.toString());
  }
  const response = NextResponse.redirect(destination);
  response.cookies.set(AUTH_SESSION_COOKIE, "", { ...secureCookieOptions, maxAge: 0 });
  response.cookies.set(AUTH_PKCE_COOKIE, "", { ...secureCookieOptions, maxAge: 0 });
  response.headers.set("Cache-Control", "no-store");
  return response;
}

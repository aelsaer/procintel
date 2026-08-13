import { createHash, randomBytes } from "node:crypto";
import { NextResponse } from "next/server";

import {
  AUTH_PKCE_COOKIE,
  discoverOidc,
  safeReturnTo,
  sealSession,
  secureCookieOptions,
  type PkceSession,
} from "@/lib/server-auth";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(request: Request) {
  try {
    const requestUrl = new URL(request.url);
    const { config, discovery, tokenEndpoint } = await discoverOidc(request.url);
    const codeVerifier = randomBytes(48).toString("base64url");
    const codeChallenge = createHash("sha256").update(codeVerifier).digest("base64url");
    const state = randomBytes(24).toString("base64url");
    const nonce = randomBytes(24).toString("base64url");
    const organizationName = requestUrl.searchParams.get("organizationName")?.trim().slice(0, 200) || undefined;
    const pkce: PkceSession = {
      codeVerifier,
      state,
      nonce,
      redirectUri: config.redirectUri,
      tokenEndpoint,
      endSessionEndpoint: discovery.end_session_endpoint,
      clientId: config.clientId,
      returnTo: safeReturnTo(requestUrl.searchParams.get("returnTo")),
      organizationName,
    };

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
    if (requestUrl.searchParams.get("intent") === "signup") authorizeUrl.searchParams.set("prompt", "create");

    const response = NextResponse.redirect(authorizeUrl);
    response.cookies.set(AUTH_PKCE_COOKIE, sealSession(pkce), {
      ...secureCookieOptions,
      maxAge: 10 * 60,
    });
    response.headers.set("Cache-Control", "no-store");
    return response;
  } catch (error) {
    return NextResponse.json({ detail: (error as Error).message }, { status: 503 });
  }
}

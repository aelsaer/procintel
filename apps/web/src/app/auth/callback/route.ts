import { NextRequest, NextResponse } from "next/server";

import {
  AUTH_PKCE_COOKIE,
  AUTH_SESSION_COOKIE,
  openSession,
  readJwtPayload,
  sealSession,
  secureCookieOptions,
  type BrowserSession,
  type PkceSession,
} from "@/lib/server-auth";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

interface TokenResponse {
  access_token?: string;
  refresh_token?: string;
  id_token?: string;
  expires_in?: number;
  error?: string;
  error_description?: string;
}

export async function GET(request: NextRequest) {
  const pkce = openSession<PkceSession>(request.cookies.get(AUTH_PKCE_COOKIE)?.value);
  if (!pkce) return NextResponse.json({ detail: "Η διαδικασία σύνδεσης έληξε. Ξεκινήστε ξανά." }, { status: 400 });
  const error = request.nextUrl.searchParams.get("error");
  if (error) {
    return NextResponse.json(
      { detail: request.nextUrl.searchParams.get("error_description") ?? error },
      { status: 400 },
    );
  }
  const code = request.nextUrl.searchParams.get("code");
  const state = request.nextUrl.searchParams.get("state");
  if (!code || !state || state !== pkce.state) {
    return NextResponse.json({ detail: "Μη έγκυρη κατάσταση σύνδεσης." }, { status: 400 });
  }

  try {
    const clientSecret = process.env.OIDC_CLIENT_SECRET;
    if (process.env.NODE_ENV === "production" && !clientSecret) {
      throw new Error("Το OIDC client secret δεν έχει ρυθμιστεί.");
    }
    const tokenResponse = await fetch(pkce.tokenEndpoint, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "authorization_code",
        code,
        redirect_uri: pkce.redirectUri,
        client_id: pkce.clientId,
        code_verifier: pkce.codeVerifier,
        ...(clientSecret ? { client_secret: clientSecret } : {}),
      }),
      cache: "no-store",
      signal: AbortSignal.timeout(10_000),
    });
    const body = (await tokenResponse.json().catch(() => ({}))) as TokenResponse;
    if (!tokenResponse.ok || !body.access_token) {
      throw new Error(body.error_description ?? body.error ?? `OIDC token endpoint: ${tokenResponse.status}`);
    }
    if (!body.id_token || readJwtPayload(body.id_token).nonce !== pkce.nonce) {
      throw new Error("Το OIDC id_token δεν αντιστοιχεί στην ενεργή διαδικασία σύνδεσης.");
    }
    const session: BrowserSession = {
      accessToken: body.access_token,
      refreshToken: body.refresh_token,
      expiresAt: Date.now() + Math.max(body.expires_in ?? 300, 30) * 1000,
      tokenEndpoint: pkce.tokenEndpoint,
      endSessionEndpoint: pkce.endSessionEndpoint,
      clientId: pkce.clientId,
    };
    const sealed = sealSession(session);
    if (sealed.length > 3800) throw new Error("Η συνεδρία του identity provider είναι υπερβολικά μεγάλη.");
    const response = NextResponse.json({ returnTo: pkce.returnTo, organizationName: pkce.organizationName });
    response.cookies.set(AUTH_SESSION_COOKIE, sealed, {
      ...secureCookieOptions,
      maxAge: 12 * 60 * 60,
    });
    response.cookies.set(AUTH_PKCE_COOKIE, "", { ...secureCookieOptions, maxAge: 0 });
    response.headers.set("Cache-Control", "no-store");
    return response;
  } catch (tokenError) {
    return NextResponse.json({ detail: (tokenError as Error).message }, { status: 502 });
  }
}

import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export function GET(request: Request) {
  const issuerUrl = process.env.OIDC_ISSUER_URL ?? process.env.NEXT_PUBLIC_OIDC_ISSUER_URL;
  const clientId = process.env.OIDC_CLIENT_ID ?? process.env.NEXT_PUBLIC_OIDC_CLIENT_ID;
  const configuredRedirect = process.env.OIDC_REDIRECT_URI ?? process.env.NEXT_PUBLIC_OIDC_REDIRECT_URI;
  const redirectUri = configuredRedirect ?? `${new URL(request.url).origin}/callback`;

  return NextResponse.json(
    issuerUrl && clientId ? { issuerUrl, clientId, redirectUri } : {},
    {
      headers: {
        "Cache-Control": "no-store",
      },
    },
  );
}

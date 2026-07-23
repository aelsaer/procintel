# apps/web

The Procintel product workspace built with Next.js App Router, TypeScript,
React, Refine, React Query and Leaflet. It uses the real FastAPI API and
tenant-persisted records; no procurement results are fabricated in the UI.

## Product surface

- **Profile**: persisted company description, server-side classification
  against all 9,454 official CPV 2008 entries plus Greek procurement aliases,
  multi-CPV targeting and tracked opportunity-score refresh. Matching falls
  back to morphological title terms when a source record has no CPV.
- **Opportunities**: tenant scores with evidence and sub-scores, saved
  pipeline, stages and priorities.
- **Alerts**: rule CRUD, filters, schedules, delivery targets, inbox,
  delivery history and digest history.
- **Competition**: persisted watches, evidence-backed participants and
  winners, inferred market cohorts and company dossiers.
- **Analytics**: scope-consistent market value and opportunity metrics,
  concentration methodology, modifications,
  payment coverage, funding, supplier trends, a real Greece/NUTS
  Leaflet map, data copilot, relationship explorer and exports. The Market
  view's precomputed cards are deliberately the platform's own answer to
  "what does a company need to know without asking" — top-buyer ranking,
  an upcoming-renewals pipeline and non-accusatory risk/anomaly indicators
  (§28) sit alongside the pre-existing supplier ranking and HHI, distinct
  from the copilot's ad-hoc question-answering. On the map (Χάρτης) view,
  clicking a NUTS region populates a "Δραστηριότητα περιοχής" panel below
  the map with the actual acts recorded there (contracts by default, or
  notices/all types via a segmented toggle), further narrowable by a
  single free-text box that auto-detects a CPV-code vs. a keyword search —
  the map's own drill-down into "what exists here", not just aggregate
  counts.
- **Archive**: loaded-data-first search, saved searches and owner/admin entity
  review with reversible merges.
- **360 profiles**: opportunity, buyer and supplier views with provenance,
  lifecycle, funding, comparable contracts, notes and tags.

The main workspace is intentionally compact: the six primary sections live in
the sidebar, while analytics and process intelligence use segmented views/tabs
instead of one long page.

## Setup

```bash
cd /home/projects/llmdi/procintel/apps/web
npm install
cp -n .env.local.example .env.local
npm run dev -- --hostname 0.0.0.0 --port 3000
```

Start FastAPI on port 8000 with `PROCINTEL_DEV_AUTH=true` for local development.
The browser uses the Next.js API proxy configured by `.env.local`.

## Verification

```bash
npx tsc --noEmit
npm run lint
npm run test:e2e
```

The Playwright suite runs every scenario for desktop and Pixel-sized mobile.
It covers persisted company radar, competition semantics, the interactive map
and copilot, loaded ADAM lookup, process evidence, market/relationship views
and similar contracts.

## Authentication

`/login` and `/callback` implement a provider-agnostic OIDC Authorization
Code + PKCE flow (`src/lib/oidc.ts`) for this SPA: `/login` discovers the
issuer's `.well-known/openid-configuration` and redirects to its
authorization endpoint (PKCE `code_verifier`/`code_challenge` via the Web
Crypto API, no new dependency); `/callback` exchanges the returned code for
an access token directly against the discovered token endpoint (a public
client, no client secret) and hands it to
`procurementAuthProvider.login()` (`src/lib/auth-provider.ts`), which
stores it exactly like the previous manual-token bootstrap did
(`localStorage["procintel_access_token"]`) and then records the §40.3
audited "login" action (`POST /v1/workspace/login`). Configured via three
env vars — `NEXT_PUBLIC_OIDC_ISSUER_URL`, `NEXT_PUBLIC_OIDC_CLIENT_ID`,
`NEXT_PUBLIC_OIDC_REDIRECT_URI` — all required together; leaving them
unset shows `/login` a clear "not configured" message instead of guessing
a provider (no specific IdP is deployed yet). The workspace topbar's
Σύνδεση/Αποσύνδεση control switches on whether `/v1/workspace/me` is
currently authenticated.

## Deployment boundary

Local development identity is supplied by `PROCINTEL_DEV_AUTH=true`. When OIDC
is not configured, `/login` presents an explicit “Είσοδος στο τοπικό
workspace” action; the application still requires that client-side session
before mounting protected workspace routes. This convenience is only for the
local deterministic owner and does not replace production authentication.
Production still requires a real OIDC provider deployment (issuer URL,
client registration) for the flow above to have something to talk to, and
the API must connect with the restricted `procintel_app` database role for
PostgreSQL RLS enforcement. Procurement data remains shared public data;
tenant workspace records are authenticated and isolated.

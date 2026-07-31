import { expect, test, type Page } from "@playwright/test";

import { apiGet, expectNoHorizontalOverflow, openSidebarView } from "./test-helpers";

const PROCESS_ID = "00000000-0000-0000-0000-000000000301";

function opportunity(index: number) {
  return {
    process_id: `00000000-0000-0000-0000-${String(index + 1).padStart(12, "0")}`,
    title: `GIS opportunity ${index + 1}`,
    buyer_name: `Public buyer ${index + 1}`,
    amount: 25_000 + index * 1_000,
    deadline: "2026-08-31T12:00:00Z",
    score: 91 - index,
    data_confidence: 0.94,
    cpv_codes: ["72212326"],
    locations: ["Αττική"],
    adam: `26PROC${String(index + 1).padStart(9, "0")}`,
    official_url: "https://cerpp.eprocurement.gov.gr/",
    document_url: "https://cerpp.eprocurement.gov.gr/",
    reasons: [{ label: "Exact CPV match" }],
  };
}

async function mockFirstSession(page: Page) {
  let completionPayload: Record<string, unknown> | null = null;

  await page.route("**/api/v1/onboarding/status", async (route) => {
    await route.fulfill({
      json: {
        required: true,
        session_id: "00000000-0000-0000-0000-000000000901",
        status: "IN_PROGRESS",
        current_step: "COMPANY",
        description: "",
        selected_cpv_codes: [],
        selected_keywords: [],
        selected_nuts_codes: [],
        quality_score: null,
        quality_findings: [],
      },
    });
  });
  await page.route("**/api/v1/onboarding/suggest", async (route) => {
    await route.fulfill({
      json: {
        session_id: "00000000-0000-0000-0000-000000000901",
        cpv_suggestions: [
          {
            id: null,
            term_type: "CPV_PREFIX",
            value: "72212326",
            label: "Υπηρεσίες ανάπτυξης λογισμικού χαρτογράφησης",
            confidence: 0.96,
            reason: "GIS and geospatial application delivery",
            source: "PROFILE_CLASSIFIER",
            is_active: true,
          },
          {
            id: null,
            term_type: "CPV_PREFIX",
            value: "71354100",
            label: "Υπηρεσίες ψηφιακής χαρτογράφησης",
            confidence: 0.91,
            reason: "Digital mapping services",
            source: "PROFILE_CLASSIFIER",
            is_active: true,
          },
        ],
        keyword_suggestions: [
          {
            id: null,
            term_type: "KEYWORD",
            value: "GIS",
            label: "GIS",
            confidence: 0.95,
            reason: "Explicit company capability",
            source: "PROFILE_CLASSIFIER",
            is_active: true,
          },
        ],
      },
    });
  });
  await page.route("**/api/v1/onboarding/complete", async (route) => {
    completionPayload = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      json: {
        session_id: "00000000-0000-0000-0000-000000000901",
        profile_id: "00000000-0000-0000-0000-000000000902",
        quality_score: 96,
        quality_findings: [],
        review_status: "REQUESTED",
        opportunities: Array.from({ length: 10 }, (_, index) => opportunity(index)),
      },
    });
  });

  return () => completionPayload;
}

test("first session turns a company description into a confirmed scope and ten opportunities", async ({ page }) => {
  const getCompletionPayload = await mockFirstSession(page);
  await page.goto("/");

  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByPlaceholder("Επωνυμία επιχείρησης").fill("Geo Systems AE");
  await page.getByLabel("Αντικείμενο").fill(
    "Αναπτύσσουμε εφαρμογές GIS, γεωχωρικές βάσεις δεδομένων και ψηφιακές χαρτογραφήσεις για δημόσιους φορείς.",
  );
  await page.getByRole("button", { name: "Εύρεση αγοράς" }).click();

  await expect(page.getByRole("heading", { name: "Επιλέξτε τι θα παρακολουθείται" })).toBeVisible();
  await expect(page.getByRole("button", { name: /72212326/ })).toHaveClass(/is-selected/);
  await page.getByLabel("Περιοχή δραστηριοποίησης").selectOption("EL30");
  await page.locator(".onboarding-review-choice input").check({ force: true });
  await page.getByRole("button", { name: "Δημιουργία shortlist" }).click();

  await expect(page.getByRole("heading", { name: "10 ευκαιρίες για πρώτη αξιολόγηση" })).toBeVisible();
  await expect(page.locator(".onboarding-opportunity")).toHaveCount(10);
  await expect(page.getByText("96% ποιότητα προφίλ")).toBeVisible();
  expect(getCompletionPayload()).toEqual(expect.objectContaining({
    company_name: "Geo Systems AE",
    selected_cpv_codes: ["72212326", "71354100"],
    selected_keywords: ["GIS"],
    selected_nuts_codes: ["EL30"],
    request_human_review: true,
  }));
  await expectNoHorizontalOverflow(page);
});

test("framework route-to-market workspace exposes economics, suppliers, and watch controls", async ({ page }) => {
  let watched = false;
  await page.route("**/api/v1/frameworks**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/watch") && route.request().method() === "POST") {
      watched = true;
      await route.fulfill({ json: { id: "watch-1", watched: true } });
      return;
    }
    await route.fulfill({
      json: {
        generated_at: "2026-07-31T10:00:00Z",
        summary: {
          framework_count: 1,
          reopening_count: 1,
          realized_spend: 420000,
          supplier_count: 1,
          ceiling_value: 1000000,
        },
        frameworks: [{
          act_id: "framework-act-1",
          process_id: PROCESS_ID,
          public_id: "26PROC019999999",
          title: "Συμφωνία-πλαίσιο υπηρεσιών GIS",
          buyer_id: "buyer-1",
          buyer_name: "Ελληνικό Κτηματολόγιο",
          cpv_codes: ["72212326"],
          status: "REOPENING",
          publication_date: "2026-07-01",
          valid_from: "2026-07-01",
          valid_until: "2026-10-01",
          days_to_expiry: 62,
          ceiling_amount: 1000000,
          realized_spend: 420000,
          utilization: 0.42,
          call_off_count: 7,
          suppliers: [{
            entity_id: "supplier-1",
            name: "Geo Supplier AE",
            lot_identifier: "LOT-1",
            awarded_value: 420000,
            membership_status: "ACTIVE",
          }],
          buyer_count: 3,
          relevance_score: 94,
          official_identifier: "26PROC019999999",
          official_url: "https://cerpp.eprocurement.gov.gr/",
          watched,
          watch_id: watched ? "watch-1" : null,
          notify_before_days: watched ? 90 : null,
        }],
        methodology: ["Framework ceiling and realized call-off spend are reported separately."],
      },
    });
  });

  await page.goto("/");
  await openSidebarView(page, "Frameworks");
  await expect(page.getByRole("heading", { name: "Συμφωνίες-πλαίσιο" })).toBeVisible();
  await expect(page.getByText("Συμφωνία-πλαίσιο υπηρεσιών GIS")).toBeVisible();
  await expect(page.getByText("Geo Supplier AE")).toBeVisible();
  await expect(page.getByText("Call-offs")).toBeVisible();
  await page.getByRole("button", { name: "Παρακολούθηση Συμφωνία-πλαίσιο υπηρεσιών GIS" }).click();
  await expect.poll(() => watched).toBe(true);
  await expect(page.getByRole("button", { name: "Αφαίρεση Συμφωνία-πλαίσιο υπηρεσιών GIS από watchlist" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("European analytics consumes the shared date and CPV scope", async ({ page }) => {
  const requestedUrls: URL[] = [];
  await page.route("**/api/v1/europe/**", async (route) => {
    const url = new URL(route.request().url());
    requestedUrls.push(url);
    if (url.pathname.endsWith("/benchmarks")) {
      await route.fulfill({ json: {
        generated_at: "2026-07-31T10:00:00Z",
        date_from: url.searchParams.get("date_from"),
        date_to: url.searchParams.get("date_to"),
        cpv_prefixes: (url.searchParams.get("cpv_prefixes") ?? "").split(",").filter(Boolean),
        covered_countries: 2,
        rows: [
          { country_code: "GR", country_name: "Ελλάδα", cpv_prefix: "77", notice_count: 40, award_count: 8, total_value: 1000000, median_value: 50000, valued_notice_count: 25, deadline_notice_count: 30, average_parse_confidence: 0.95 },
          { country_code: "CY", country_name: "Κύπρος", cpv_prefix: "77", notice_count: 12, award_count: 2, total_value: 250000, median_value: 35000, valued_notice_count: 8, deadline_notice_count: 10, average_parse_confidence: 0.92 },
        ],
        methodology: ["TED cohort by publication date, country and CPV division."],
      } });
      return;
    }
    await route.fulfill({ json: {
      generated_at: "2026-07-31T10:00:00Z",
      profile_version: 4,
      candidates_seen: 12,
      matches: [{
        act_id: "ted-act-1",
        process_id: null,
        ted_notice_id: "123456-2026",
        publication_number: "123456-2026",
        official_url: "https://ted.europa.eu/en/notice/-/detail/123456-2026",
        title: "Vegetation management services",
        buyer_name: "Cyprus public authority",
        country_code: "CY",
        country_name: "Κύπρος",
        cpv_codes: ["77312000"],
        estimated_value: 250000,
        currency: "EUR",
        publication_date: "2026-07-28",
        submission_deadline: "2026-08-20T12:00:00Z",
        match_score: 91,
        reasons: ["Exact CPV profile match"],
        barriers: ["Cross-border delivery"],
        parse_confidence: 0.94,
        computed_at: "2026-07-31T10:00:00Z",
      }],
      methodology: ["Only evidence-backed cross-border matches are returned."],
    } });
  });

  await page.goto("/");
  await openSidebarView(page, "Ευκαιρίες");
  await expect(page.locator(".workspace-scope-bar")).toHaveCount(1);
  const expectedDateFrom = await page.getByLabel("Κοινή ημερομηνία από").inputValue();
  const expectedDateTo = await page.getByLabel("Κοινή ημερομηνία έως").inputValue();
  await openSidebarView(page, "Analytics");
  await page.getByRole("button", { name: "Ευρώπη" }).click();

  await expect(page.getByRole("heading", { name: "Η αγορά ανά χώρα" })).toBeVisible();
  await expect(page.getByLabel("Η αγορά ανά χώρα").getByText("Κύπρος", { exact: true })).toBeVisible();
  await expect(page.getByText("Vegetation management services")).toBeVisible();
  await expect.poll(() => requestedUrls.some((url) =>
    url.pathname.endsWith("/benchmarks")
      && url.searchParams.get("date_from") === expectedDateFrom
      && url.searchParams.get("date_to") === expectedDateTo
      && Boolean(url.searchParams.get("cpv_prefixes")),
  )).toBe(true);
  await expect.poll(() => requestedUrls.some((url) =>
    url.pathname.endsWith("/opportunities")
      && url.searchParams.get("date_from") === expectedDateFrom
      && url.searchParams.get("date_to") === expectedDateTo,
  )).toBe(true);
  await expectNoHorizontalOverflow(page);
});

test("committee report, proposal production, and daily document tools are reachable", async ({ page, request }) => {
  await page.goto(`/processes/${PROCESS_ID}`);
  await page.getByRole("button", { name: "BID / NO-BID" }).click();
  const report = page.getByRole("dialog", { name: "BID / NO-BID" });
  await expect(report).toBeVisible();
  await expect(report.getByText("confidence")).toBeVisible();
  await expect(report.getByRole("heading", { name: "Κίνδυνοι και blockers" })).toBeVisible();
  await expect(report.getByRole("heading", { name: "Υποχρεωτικές απαιτήσεις" })).toBeVisible();
  await expect(report.getByRole("heading", { name: "Incumbent και ανταγωνιστές" })).toBeVisible();
  await expect(report.getByRole("button", { name: "PDF" })).toBeEnabled();
  await report.getByRole("button", { name: "Κλείσιμο report" }).click();

  await page.getByRole("button", { name: "Bid workspace", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Τεχνική προσφορά" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Word" }).last()).toBeVisible();
  await expect(page.getByRole("button", { name: "Βιβλιοθήκη", exact: true })).toBeVisible();

  const documentSearch = await apiGet<{ data: Array<{ process_id: string | null }> }>(
    request,
    "/v1/search?q=26PROC019308569",
  );
  const documentProcessId = documentSearch.data.find((item) => item.process_id)?.process_id;
  expect(documentProcessId).toBeTruthy();
  await page.goto(`/processes/${documentProcessId}`);
  await page.getByRole("button", { name: "Documents", exact: true }).click();
  await expect(page.getByText("Document tools", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Bulk ZIP" })).toBeVisible();
  await expect(page.getByLabel("Έγγραφο για μετατροπή σε Word")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("commercial workspace and public SaaS pages expose plans, support, help, and status", async ({ page }) => {
  await page.goto("/settings");
  await page.getByRole("button", { name: "Πλάνο και υποστήριξη" }).click();
  await expect(page.getByText("Professional", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Onboarding plan")).toBeVisible();
  await expect(page.getByText("Support", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Θέμα support ticket")).toBeVisible();

  await page.goto("/pricing");
  await expect(page.getByRole("heading", { name: "Procurement intelligence για κάθε στάδιο ανάπτυξης" })).toBeVisible();
  await expect(page.getByText("14 ημέρες δωρεάν.")).toBeVisible();
  await expect(page.getByRole("link", { name: "Δωρεάν δοκιμή" })).toBeVisible();

  await page.goto("/help");
  await expect(page.getByRole("heading", { name: "Σύντομες, πρακτικές ροές εργασίας" })).toBeVisible();
  await expect(page.getByText("Χρειάζεστε ανθρώπινη βοήθεια;")).toBeVisible();

  await page.goto("/status");
  await expect(page.getByRole("heading", { name: /Όλα τα συστήματα λειτουργούν|Υπάρχει υποβάθμιση υπηρεσίας/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Ενεργά περιστατικά" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

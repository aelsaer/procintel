import { expect, test, type APIRequestContext } from "@playwright/test";
import { apiDelete, apiGet, expectNoHorizontalOverflow, openSidebarView } from "./test-helpers";

type BusinessProfile = {
  company_name: string | null;
  description: string;
  cpv_prefixes: string[];
  keywords: string[];
  nuts_codes: string[];
  municipality: string | null;
  buyer_types: string[];
  procedure_types: string[];
  amount_min: string | number | null;
  amount_max: string | number | null;
};

type PipelineItem = {
  id: string;
  process_id: string;
  stage: string;
  priority: string;
  expected_value: string | number | null;
  next_action: string | null;
  due_at: string | null;
};

type Opportunity = { process_id: string; title: string | null };
type SavedSearch = { id: string; name: string };
type AlertRule = { id: string; name: string };
type Watch = { id: string; object_id: string; object_type: "COMPETITOR" };

async function restoreProfile(request: APIRequestContext, profile: BusinessProfile) {
  const response = await request.put("/api/v1/business-profile", {
    data: {
      company_name: profile.company_name,
      description: profile.description,
      cpv_prefixes: profile.cpv_prefixes,
      keywords: profile.keywords,
      nuts_codes: profile.nuts_codes,
      municipality: profile.municipality,
      buyer_types: profile.buyer_types,
      procedure_types: profile.procedure_types,
      amount_min: profile.amount_min,
      amount_max: profile.amount_max,
      classify: true,
    },
  });
  expect(response.ok()).toBeTruthy();
  const restored = await response.json() as { updated_at: string };
  const requestedAfter = new Date(restored.updated_at).getTime() - 1_000;
  await expect.poll(async () => {
    const scoring = await apiGet<{ status: string; requested_at: string | null }>(request, "/v1/business-profile/scoring-status");
    const current = scoring.requested_at !== null && new Date(scoring.requested_at).getTime() >= requestedAfter;
    return current ? scoring.status : "STALE";
  }, { timeout: 45_000 }).toBe("SUCCEEDED");
}

test("sidebar, global search, and keyboard shortcut reach every primary workspace", async ({ page }) => {
  await page.goto("/");

  const destinations = [
    ["Εταιρικό προφίλ", "Δραστηριότητα και μόνιμη στόχευση"],
    ["Ευκαιρίες", "Ευκαιρίες radar"],
    ["Alerts", "Κανόνες και ειδοποιήσεις"],
    ["Ανταγωνισμός", "Ανταγωνιστικό τοπίο"],
    ["Frameworks", "Συμφωνίες-πλαίσιο"],
    ["Analytics", "Ανάλυση αγοράς"],
    ["Αρχείο", "Αναζήτηση στο φορτωμένο αρχείο"],
  ] as const;

  for (const [button, heading] of destinations) {
    await openSidebarView(page, button);
    await expect(page.getByRole("heading", { name: heading })).toBeVisible();
  }

  await openSidebarView(page, "Εταιρικό προφίλ");
  await page.keyboard.press("Control+k");
  const archiveInput = page.getByLabel("Αναζήτηση στο αρχείο");
  await expect(archiveInput).toBeFocused();
  await expect(page.getByRole("heading", { name: "Αναζήτηση στο φορτωμένο αρχείο" })).toBeVisible();

  await openSidebarView(page, "Εταιρικό προφίλ");
  await page.getByRole("button", { name: /Αναζήτηση ΑΔΑ/ }).click();
  await expect(archiveInput).toBeFocused();
  await expectNoHorizontalOverflow(page);
});

test("date and geography scope propagates across workspaces", async ({ page }) => {
  const profileLoaded = page.waitForResponse((response) =>
    response.url().includes("/api/v1/business-profile") && response.request().method() === "GET",
  );
  await page.goto("/");
  await profileLoaded;
  await openSidebarView(page, "Ευκαιρίες");

  await page.getByLabel("Κοινή περιοχή workspace").selectOption("EL43");
  await page.getByLabel("Κοινός δήμος ή νομός workspace").fill("Ηράκλειο");
  await page.getByLabel("Κοινή ημερομηνία από").fill("2026-07-28");
  await page.getByLabel("Κοινή ημερομηνία έως").fill("2026-07-29");

  const radarRequest = page.waitForRequest((request) => {
    if (!request.url().includes("/api/v1/intelligence/opportunities")) return false;
    const params = new URL(request.url()).searchParams;
    return params.get("nuts_code") === "EL43"
      && params.get("municipality") === "Ηράκλειο"
      && params.get("date_from") === "2026-07-28"
      && params.get("date_to") === "2026-07-29";
  });
  await page.getByRole("button", { name: "Εφαρμογή" }).click();
  await radarRequest;
  await expect(page.getByLabel("Κοινή περιοχή workspace")).toHaveValue("EL43");
  await expect(page.getByLabel("Κοινή ημερομηνία από")).toHaveValue("2026-07-28");

  const competitionRequest = page.waitForRequest((request) => {
    if (!request.url().includes("/api/v1/competitors/discover")) return false;
    const params = new URL(request.url()).searchParams;
    return params.get("date_from") === "2026-07-28"
      && params.get("date_to") === "2026-07-29"
      && params.get("nuts_code") === "EL43"
      && params.get("municipality") === "Ηράκλειο";
  });
  await openSidebarView(page, "Ανταγωνισμός");
  await competitionRequest;
  await expect(page.locator(".competition-scope")).toContainText("2026-07-28 - 2026-07-29");

  await openSidebarView(page, "Alerts");
  await expect(page.locator(".active-filter-strip")).toContainText("Ηράκλειο");
  await expect(page.locator(".active-filter-strip")).toContainText("2026-07-28 - 2026-07-29");

  await openSidebarView(page, "Analytics");
  const relationshipRequest = page.waitForRequest((request) => {
    if (!request.url().includes("/api/v1/intelligence/relationships")) return false;
    const params = new URL(request.url()).searchParams;
    return params.get("nuts_code") === "EL43"
      && params.get("municipality") === "Ηράκλειο"
      && params.get("date_from") === "2026-07-28"
      && params.get("date_to") === "2026-07-29";
  });
  await page.getByRole("button", { name: "Σχέσεις" }).click();
  await relationshipRequest;

  await openSidebarView(page, "Αρχείο");
  const archiveInput = page.getByLabel("Αναζήτηση στο αρχείο");
  await archiveInput.fill("GIS");
  const lexicalRequest = page.waitForRequest((request) => {
    if (!request.url().includes("/api/v1/search?")) return false;
    const params = new URL(request.url()).searchParams;
    return params.get("q") === "GIS"
      && params.get("nuts_code") === "EL43"
      && params.get("municipality") === "Ηράκλειο"
      && params.get("date_from") === "2026-07-28"
      && params.get("date_to") === "2026-07-29";
  });
  await page.getByRole("button", { name: "Έλεγχος" }).click();
  await lexicalRequest;

  await archiveInput.fill("17PROC001636130");
  const exactRequest = page.waitForRequest((request) => {
    if (!request.url().includes("/api/v1/search?")) return false;
    return new URL(request.url()).searchParams.get("q") === "17PROC001636130";
  });
  await page.getByRole("button", { name: "Έλεγχος" }).click();
  const exactParams = new URL((await exactRequest).url()).searchParams;
  expect(exactParams.has("date_from")).toBeFalsy();
  expect(exactParams.has("nuts_code")).toBeFalsy();
});

test("business profile classification persists meaningful targeting", async ({ page, request }) => {
  const original = await apiGet<BusinessProfile>(request, "/v1/business-profile");
  const description = "Αναλαμβάνουμε αποψιλώσεις, καθαρισμούς οικοπέδων, κοπή χόρτων και κλάδεμα δέντρων στην Αττική.";

  try {
    await page.goto("/");
    const descriptionInput = page.getByLabel("Περιγραφή δραστηριότητας");
    await expect(descriptionInput).toHaveValue(original.description);
    await descriptionInput.fill(description);
    await expect(page.getByRole("button", { name: /Υπηρεσίες εκκαθάρισης από αγριόχορτα/ })).toBeVisible();
    await expect(page.getByRole("button", { name: /Κλάδεμα δέντρων/ })).toBeVisible();
    await expect(page.getByText(/κατηγορίες εντοπίστηκαν/)).toBeVisible();
    for (const suggestion of [
      page.getByRole("button", { name: /Υπηρεσίες εκκαθάρισης από αγριόχορτα/ }),
      page.getByRole("button", { name: /Κλάδεμα δέντρων/ }),
    ]) {
      if (!(await suggestion.getAttribute("class"))?.includes("is-selected")) {
        await suggestion.click();
      }
    }
    await expect(page.getByLabel("Ενεργοί κωδικοί CPV").getByText("CPV 77312000")).toBeVisible();
    await page.getByLabel("Keyword").fill("αποψιλ");
    await page.getByLabel("Περιφέρεια").selectOption("EL30");
    await page.getByText("Προηγμένα φίλτρα αγοράς").click();
    await page.getByLabel("Ελάχιστο ποσό").fill("0");

    const saved = page.waitForResponse((response) =>
      response.url().includes("/api/v1/business-profile") && response.request().method() === "PUT",
    );
    await page.getByRole("button", { name: "Έλεγχος και εφαρμογή" }).click();
    await expect(page.getByRole("dialog", { name: "Επιβεβαίωση εταιρικού προφίλ" })).toBeVisible();
    await page.getByRole("button", { name: "Επιβεβαίωση και επαναϋπολογισμός" }).click();
    expect((await saved).ok()).toBeTruthy();
    await expect(page.getByRole("heading", { name: "Ευκαιρίες radar" })).toBeVisible();

    const persisted = await apiGet<BusinessProfile>(request, "/v1/business-profile");
    expect(persisted.description).toBe(description);
    expect(persisted.nuts_codes).toContain("EL30");
    expect(Number(persisted.amount_min)).toBe(0);
    expect(persisted.cpv_prefixes[0]).toBe("77312000");
    expect(persisted.cpv_prefixes).toContain("77341000");
    await expect(page.getByRole("status").filter({ hasText: /Το radar ενημερώθηκε με/ })).toBeVisible();
    const scoring = await apiGet<{ status: string; reason: string }>(request, "/v1/business-profile/scoring-status");
    expect(scoring.status).toBe("SUCCEEDED");
    expect(scoring.reason).toBe("BUSINESS_PROFILE_CHANGED");

    const competitionRequest = page.waitForRequest((request) => {
      if (!request.url().includes("/api/v1/competitors/discover")) return false;
      const params = new URL(request.url()).searchParams;
      return params.get("cpv_prefixes")?.split(",").includes("77312000") ?? false;
    },
    );
    await openSidebarView(page, "Ανταγωνισμός");
    const competitionParams = new URL((await competitionRequest).url()).searchParams;
    expect(competitionParams.get("cpv_prefixes")?.split(",")).toEqual(
      expect.arrayContaining(persisted.cpv_prefixes),
    );
    expect(competitionParams.get("keywords")?.split(",")).toEqual(
      expect.arrayContaining(persisted.keywords),
    );
    expect(competitionParams.get("taxonomy_match")).toBe("ANY");
    await expect(page.getByLabel("Ενεργό επιχειρηματικό scope")).toContainText("CPV 77312000");
    await expect(page.locator(".competition-scope")).toContainText("Τρέχον διάστημα");

    await openSidebarView(page, "Alerts");
    await expect(page.locator(".active-filter-strip")).toContainText(`${persisted.cpv_prefixes.length} CPV`);
    await expect(page.locator(".active-filter-strip")).toContainText(persisted.keywords[0]);

    await openSidebarView(page, "Analytics");
    const relationshipRequest = page.waitForRequest((request) =>
      request.url().includes("/api/v1/intelligence/relationships"),
    );
    await page.getByRole("button", { name: "Σχέσεις" }).click();
    const relationshipParams = new URL((await relationshipRequest).url()).searchParams;
    expect(relationshipParams.get("cpv_prefixes")?.split(",")).toEqual(
      expect.arrayContaining(persisted.cpv_prefixes),
    );
    expect(relationshipParams.get("keywords")?.split(",")).toEqual(
      expect.arrayContaining(persisted.keywords),
    );
  } finally {
    await restoreProfile(request, original);
  }
});

test("opportunity can be saved, staged, persisted, and restored", async ({ page, request }) => {
  const originalProfile = await apiGet<BusinessProfile>(request, "/v1/business-profile");
  await restoreProfile(request, {
    ...originalProfile,
    description: "GIS",
    cpv_prefixes: ["38221000"],
    keywords: ["gis"],
    nuts_codes: [],
    municipality: null,
    amount_min: 0,
    amount_max: null,
  });
  const opportunities = await apiGet<Opportunity[]>(request, "/v1/intelligence/opportunities?limit=10");
  expect(opportunities.length).toBeGreaterThan(0);
  const target = opportunities[0];
  const initial = await apiGet<PipelineItem[]>(request, "/v1/workspace/pipeline");
  const previous = initial.find((item) => item.process_id === target.process_id) ?? null;
  if (previous) await apiDelete(request, `/v1/workspace/pipeline/${previous.id}`);
  let createdId: string | null = null;

  try {
    await page.goto("/");
    await openSidebarView(page, "Ευκαιρίες");
    const saveButton = page.getByRole("button", { name: "Αποθήκευση στο pipeline" }).first();
    await expect(saveButton).toBeVisible();
    const created = page.waitForResponse((response) =>
      response.url().includes("/api/v1/workspace/pipeline") && response.request().method() === "POST",
    );
    await saveButton.click();
    expect((await created).status()).toBe(201);
    await expect(page.getByRole("button", { name: "Αποθηκευμένη ευκαιρία" }).first()).toBeDisabled();

    await page.getByRole("button", { name: "Pipeline", exact: true }).click();
    const stage = page.getByLabel(new RegExp(`Stage για ${target.title?.slice(0, 25) ?? ""}`)).first();
    await expect(stage).toBeVisible();
    await stage.selectOption("BIDDING");
    await expect(stage).toHaveValue("BIDDING");

    const after = await apiGet<PipelineItem[]>(request, "/v1/workspace/pipeline");
    const createdItem = after.find((item) => item.process_id === target.process_id);
    expect(createdItem?.stage).toBe("BIDDING");
    createdId = createdItem?.id ?? null;
  } finally {
    if (createdId) await apiDelete(request, `/v1/workspace/pipeline/${createdId}`);
    if (previous) {
      const response = await request.post("/api/v1/workspace/pipeline", {
        data: {
          process_id: previous.process_id,
          stage: previous.stage,
          priority: previous.priority,
          expected_value: previous.expected_value,
          next_action: previous.next_action,
          due_at: previous.due_at,
        },
      });
      expect(response.ok()).toBeTruthy();
    }
    await restoreProfile(request, originalProfile);
  }
});

test("archive detects identifier types and saved searches round-trip", async ({ page, request }) => {
  const query = `e2e-meaningful-search-${Date.now()}`;

  try {
    await page.goto("/");
    await openSidebarView(page, "Αρχείο");
    const input = page.getByLabel("Αναζήτηση στο αρχείο");
    await input.fill("6ΙΖ07Λ7-ΕΨΒ");
    await expect(page.locator(".query-kind")).toHaveText("ΑΔΑ");
    await input.fill("17PROC001636130");
    await expect(page.locator(".query-kind")).toHaveText("ΑΔΑΜ");
    await input.fill("090000045");
    await expect(page.locator(".query-kind")).toHaveText("ΑΦΜ");
    await input.fill("GIS");
    await page.getByRole("button", { name: "Έλεγχος" }).click();
    const gisResults = page.locator(".archive-results .result-card");
    await expect(gisResults.first()).toBeVisible();
    expect(await gisResults.allTextContents()).not.toEqual(
      expect.arrayContaining([expect.stringMatching(/LOGISTICS/i)]),
    );
    await input.fill(query);
    await expect(page.locator(".query-kind")).toHaveText("Λεκτικό τίτλου");
    await page.getByRole("button", { name: "Έλεγχος" }).click();
    await expect(page.getByText("Δεν υπάρχει στα φορτωμένα δεδομένα")).toBeVisible();

    await page.getByRole("button", { name: "Αποθήκευση" }).click();
    const savedButton = page.getByLabel("Αποθηκευμένες αναζητήσεις").getByRole("button", { name: query, exact: true });
    await expect(savedButton).toBeVisible();
    await page.getByRole("button", { name: `Διαγραφή ${query}` }).click();
    await expect(savedButton).toHaveCount(0);
  } finally {
    const searches = await apiGet<SavedSearch[]>(request, "/v1/workspace/saved-searches");
    for (const search of searches.filter((item) => item.name === query)) {
      await apiDelete(request, `/v1/workspace/saved-searches/${search.id}`);
    }
  }
});

test("alert rule supports create, edit, pause, and archive", async ({ page, request }) => {
  const name = `E2E alert ${Date.now()}`;
  const editedName = `${name} edited`;
  const profile = await apiGet<BusinessProfile>(request, "/v1/business-profile");

  try {
    await page.goto("/");
    await openSidebarView(page, "Alerts");
    await page.getByLabel("Όνομα", { exact: true }).fill(name);
    await page.getByLabel("Συχνότητα").selectOption("WEEKLY_DIGEST");
    await page.getByLabel("Ώρα digest").fill("09:15");
    await page.getByLabel("Κανάλι παράδοσης").selectOption("EMAIL");
    await page.getByLabel("Προορισμός, προαιρετικός").fill("e2e@example.test");
    const createResponse = page.waitForResponse((response) =>
      response.url().includes("/api/v1/alert-rules") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Δημιουργία κανόνα" }).click();
    const response = await createResponse;
    expect(response.status()).toBe(201);
    const createBody = response.request().postDataJSON() as {
      filters: { cpv_prefixes?: string[]; keywords?: string[]; taxonomy_match_any?: boolean; taxonomy_match_mode?: string };
    };
    expect(createBody.filters.cpv_prefixes ?? []).toEqual(profile.cpv_prefixes);
    expect(createBody.filters.keywords ?? []).toEqual(profile.keywords);
    if (profile.cpv_prefixes.length && profile.keywords.length) {
      expect(createBody.filters.taxonomy_match_mode).toBe("KEYWORD_REQUIRED");
    } else {
      expect(Boolean(createBody.filters.taxonomy_match_any)).toBe(Boolean(profile.cpv_prefixes.length || profile.keywords.length));
    }

    let row = page.locator(".alert-rule-row").filter({ hasText: name });
    await expect(row).toBeVisible();
    await row.getByRole("button", { name: "Επεξεργασία κανόνα" }).click();
    await page.getByLabel("Όνομα", { exact: true }).fill(editedName);
    await page.getByLabel("Συχνότητα").selectOption("IMMEDIATE");
    await expect(page.getByLabel("Ώρα digest")).toHaveCount(0);
    await page.getByRole("button", { name: "Ενημέρωση κανόνα" }).click();

    row = page.locator(".alert-rule-row").filter({ hasText: editedName });
    await expect(row).toBeVisible();
    await row.getByRole("button", { name: "Παύση κανόνα" }).click();
    await expect(row.getByRole("button", { name: "Ενεργοποίηση κανόνα" })).toBeVisible();
    await row.getByRole("button", { name: "Αρχειοθέτηση κανόνα" }).click();
    await expect(row).toHaveCount(0);
  } finally {
    const rules = await apiGet<AlertRule[]>(request, "/v1/alert-rules");
    for (const rule of rules.filter((item) => item.name.startsWith(name))) {
      await apiDelete(request, `/v1/alert-rules/${rule.id}`);
    }
  }
});

test("competitor watch toggles and restores persisted state", async ({ page, request }) => {
  const initial = await apiGet<Watch[]>(request, "/v1/workspace/watches?object_type=COMPETITOR");

  try {
    await page.goto("/");
    await openSidebarView(page, "Ανταγωνισμός");
    const toggle = page.locator(".competitor-watch").first();
    if (!(await toggle.isVisible())) {
      await page.getByRole("button", { name: "Ανάδοχοι market", exact: true }).click();
    }
    if (!(await toggle.isVisible())) {
      await page.getByRole("button", { name: "Όλη η βάση", exact: true }).click();
    }
    await expect(toggle).toBeVisible();
    const initialLabel = await toggle.getAttribute("aria-label");
    await toggle.click();
    await expect(toggle).not.toHaveAttribute("aria-label", initialLabel ?? "");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-label", initialLabel ?? "");
  } finally {
    const current = await apiGet<Watch[]>(request, "/v1/workspace/watches?object_type=COMPETITOR");
    const initialIds = new Set(initial.map((item) => item.object_id));
    const currentIds = new Set(current.map((item) => item.object_id));
    for (const item of current.filter((watch) => !initialIds.has(watch.object_id))) {
      await apiDelete(request, `/v1/workspace/watches/${item.id}`);
    }
    for (const item of initial.filter((watch) => !currentIds.has(watch.object_id))) {
      const response = await request.post("/api/v1/workspace/watches", {
        data: { object_id: item.object_id, object_type: "COMPETITOR" },
      });
      expect(response.ok()).toBeTruthy();
    }
  }
});

test("analytics controls produce evidence, filtered relationships, and refreshed sources", async ({ page }) => {
  await page.goto("/");
  await openSidebarView(page, "Analytics");

  await page.getByRole("button", { name: "Αγορά" }).click();
  await page.getByRole("button", { name: "Μεθοδολογία" }).click();
  const methodology = page.getByRole("dialog", { name: /HHI|Μεθοδολογία/ });
  await expect(methodology).toBeVisible();
  await expect(methodology).toContainText("Περιορισμοί");
  await methodology.getByRole("button", { name: "Κλείσιμο" }).click();

  await page.getByRole("button", { name: "Σχέσεις" }).click();
  await page.getByText("Φίλτρα σχέσεων").click();
  await page.getByLabel("Σχέση").selectOption("AWARDED_TO");
  await page.getByLabel("Ελάχιστη αξία").fill("10000");
  await page.getByLabel("Πηγή").selectOption("act_parties");
  await page.getByLabel("Confidence").selectOption("0.85");
  await page.getByRole("button", { name: "Table" }).click();
  await expect(page.getByRole("table")).toBeVisible();

  await page.getByRole("button", { name: "Πηγές" }).click();
  const refreshed = page.waitForResponse((response) =>
    response.url().includes("/api/v1/analytics/data-coverage") && response.request().method() === "GET",
  );
  await page.getByRole("button", { name: "Ανανέωση κατάστασης εισαγωγής" }).click();
  expect((await refreshed).ok()).toBeTruthy();
});

test("CSV export reaches a downloadable terminal state", async ({ page, request }) => {
  type ExportJob = { id: string; status: string; download_url: string | null; format: string };
  const before = await apiGet<ExportJob[]>(request, "/v1/exports");
  const beforeIds = new Set(before.map((job) => job.id));

  await page.goto("/");
  await openSidebarView(page, "Analytics");
  await page.getByRole("button", { name: "Exports" }).click();
  await page.getByRole("button", { name: "CSV", exact: true }).click();

  await expect.poll(async () => {
    const jobs = await apiGet<ExportJob[]>(request, "/v1/exports");
    return jobs.find((job) => !beforeIds.has(job.id))?.status;
  }, { timeout: 15_000 }).toBe("SUCCEEDED");
  await page.getByRole("button", { name: "Ανανέωση exports" }).click();
  await expect(page.getByRole("link", { name: "Λήψη export" }).first()).toBeVisible();
});

test("entity review generation is available without forcing a merge", async ({ page }) => {
  await page.goto("/");
  await openSidebarView(page, "Αρχείο");
  await page.getByRole("button", { name: "Entity review" }).click();
  await expect(page.getByRole("heading", { name: "Entity resolution review" })).toBeVisible();
  const generated = page.waitForResponse((response) =>
    response.url().includes("/api/v1/entity-review/generate") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Παραγωγή υποψηφίων" }).click();
  expect((await generated).ok()).toBeTruthy();
  await expect(page.getByText("Ιστορικό συγχωνεύσεων")).toBeVisible();
});

test("API failures and empty results have actionable UI states", async ({ page }) => {
  await page.route("**/api/v1/analytics/data-coverage", async (route) => {
    await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "coverage unavailable" }) });
  });
  await page.goto("/");
  await expect(page.getByText("Δεν είναι διαθέσιμη η κατάσταση εισαγωγής")).toBeVisible();

  await openSidebarView(page, "Αρχείο");
  await page.getByLabel("Αναζήτηση στο αρχείο").fill("definitely-no-procurement-record-xyz");
  await page.getByRole("button", { name: "Έλεγχος" }).click();
  await expect(page.getByText("Δεν υπάρχει στα φορτωμένα δεδομένα")).toBeVisible();
});

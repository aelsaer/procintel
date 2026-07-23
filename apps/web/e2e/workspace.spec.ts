import { expect, test } from "@playwright/test";

async function expectNoHorizontalOverflow(page: import("@playwright/test").Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
}

test("company radar is the primary workspace", async ({ page }, testInfo) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Τι θέλεις να παρακολουθεί το Procintel;" })).toBeVisible();
  await expect(page.getByRole("navigation").getByRole("button")).toHaveCount(6);
  await expect(page.getByText("Radar ενεργό")).toBeVisible();
  await expect(page.getByLabel("Προτεινόμενες κατηγορίες CPV").getByRole("button").first()).toBeVisible();
  await expectNoHorizontalOverflow(page);

  await page.screenshot({ path: testInfo.outputPath("home.png"), fullPage: true });
});

test("competitor intelligence distinguishes evidence from market inference", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Ανταγωνισμός" }).click();

  await expect(page.getByRole("heading", { name: "Ανταγωνιστικό τοπίο" })).toBeVisible();
  await expect(page.getByLabel("Σύνοψη ανταγωνισμού")).toBeVisible();
  await expect(page.getByText(/Οι συμμετοχές\/ανάδοχοι είναι τεκμηριωμένα facts/)).toBeVisible();
  await expect(page.getByText(/market competitors είναι εκτίμηση/)).toBeVisible();
  await expectNoHorizontalOverflow(page);

  await page.screenshot({ path: testInfo.outputPath("competitors.png"), fullPage: true });
});

test("Leaflet NUTS map, region ranking, and copilot stay synchronized", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Analytics" }).click();
  await page.getByRole("button", { name: "Χάρτης" }).click();

  const map = page.getByTestId("greece-nuts-map");
  await expect(map).toBeVisible();
  await expect(map.locator("xpath=..")).toHaveAttribute("data-map-status", "ready");
  await expect(map.locator("[data-nuts-code]")).toHaveCount(13);

  await map.locator('[data-nuts-code="EL43"]').click({ force: true });
  await expect(page.locator(".region-ranking button.is-active")).toContainText("Κρήτη");
  await expect(page.locator("#region-activity-title")).toContainText("Κρήτη");

  await page.getByRole("button", { name: "Όλα" }).click();
  await page.getByLabel(/Αναζήτηση δραστηριότητας περιοχής/).fill("υπηρεσ");
  await expect(page.locator(".region-activity-panel")).not.toContainText("Ανάγνωση δραστηριότητας περιοχής");

  const copilotHeightBefore = await page.locator(".copilot-panel").evaluate((element) => element.getBoundingClientRect().height);
  await page.getByLabel("Ερώτηση analytics").fill("Δείξε Θεσσαλία στον χάρτη");
  await page.getByRole("button", { name: "Αποστολή ερώτησης" }).click();
  await expect(page.locator(".region-ranking button.is-active")).toContainText("Θεσσαλία");
  await expect(page.locator("#region-activity-title")).toContainText("Θεσσαλία");
  await expect(page.locator(".chat-log")).toContainText("Εστίασα τον χάρτη στην περιοχή Θεσσαλία");
  const copilotHeightAfter = await page.locator(".copilot-panel").evaluate((element) => element.getBoundingClientRect().height);
  expect(copilotHeightAfter).toBe(copilotHeightBefore);
  await expectNoHorizontalOverflow(page);

  await page.screenshot({ path: testInfo.outputPath("analytics.png"), fullPage: true });
});

test("archive returns loaded ADAM records without provider waiting", async ({ page }) => {
  await page.goto("/?q=17PROC001636130");

  await expect(page.getByText("Ακριβής ταύτιση")).toBeVisible();
  await expect(page.getByText("17PROC001636130")).toBeVisible();
  await expect(page.getByText(/EΡΓΑΣΙΕΣ ΑΠΟΨΙΛΩΣΗΣ/)).toBeVisible();
  await expect(page.getByLabel("Κατάσταση εισαγωγής δεδομένων")).toBeVisible();
});

test("process 360 exposes confirmed competition evidence", async ({ page }) => {
  await page.goto("/processes/00000000-0000-0000-0000-000000000301");
  await page.getByRole("button", { name: "Competitors" }).click();

  await expect(page.getByRole("heading", { name: "Ανταγωνιστικό τοπίο" })).toBeVisible();
  await expect(page.getByText("Τεκμηριωμένα facts")).toBeVisible();
  await expect(page.getByText("ΑΛΦΑ ΚΑΘΑΡΙΣΜΟΙ ΙΚΕ").last()).toBeVisible();
  await expect(page.getByText("Ανάδοχος", { exact: true }).last()).toBeVisible();
  await expect(page.getByText(/δεν αποτελεί δήλωση συμμετοχής/i)).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("market intelligence and relationship explorer expose operational views", async ({ page, request }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Analytics" }).click();

  await expect(page.getByRole("heading", { name: "Ανάλυση αγοράς" })).toBeVisible();
  await page.getByRole("button", { name: "Αγορά" }).click();
  const marketMetrics = page.getByLabel("Market metrics");
  await expect(marketMetrics).toBeVisible();
  await expect(marketMetrics.getByText("Ευκαιρίες", { exact: true })).toBeVisible();
  await expect(marketMetrics.getByText(/δημοσιευμένες προκηρύξεις/)).toBeVisible();
  await expect(page.getByLabel("Market concentration methodology")).toBeVisible();
  const overview = await request.get("/api/v1/analytics/market-overview");
  expect(overview.ok()).toBeTruthy();
  const totals = await overview.json();
  expect(totals.notice_count).toBeLessThanOrEqual(totals.opportunity_count);
  expect(totals.opportunity_count).toBeLessThanOrEqual(totals.act_count);

  await page.getByRole("button", { name: "Σχέσεις" }).click();
  await expect(page.getByRole("heading", { name: "Relationship Explorer" })).toBeVisible();
  await expect(page.getByText("Canonical procurement graph")).toBeVisible();
  await page.getByRole("button", { name: "Table" }).click();
  await expect(page.getByRole("table")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("source coverage exposes imported records, links, and connector runs", async ({ page }, testInfo) => {
  await page.goto("/");
  await expect(page.getByLabel("Κατάσταση εισαγωγής δεδομένων")).toBeVisible();
  await page.getByRole("button", { name: "Analytics" }).click();
  await page.getByRole("button", { name: "Πηγές" }).click();

  await expect(page.getByRole("heading", { name: "Πηγές και συνδέσεις δεδομένων" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Φορτωμένες πηγές" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Cross-source συνδέσεις" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Πρόσφατα ingestion windows" })).toBeVisible();
  await expect(page.getByText("ΚΗΜΔΗΣ", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Διαύγεια", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("ΓΕΜΗ", { exact: true }).first()).toBeVisible();
  await expectNoHorizontalOverflow(page);

  await page.screenshot({ path: testInfo.outputPath("sources.png"), fullPage: true });
});

test("process 360 provides comparable contracts as a separate intelligence view", async ({ page }) => {
  await page.goto("/processes/00000000-0000-0000-0000-000000000301");
  await page.getByRole("button", { name: "Similar contracts" }).click();

  await expect(page.getByRole("heading", { name: "Similar contracts" })).toBeVisible();
  await expect(page.getByText("CPV, buyer, title and value cohort")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

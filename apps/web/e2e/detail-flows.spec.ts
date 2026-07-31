import { expect, test } from "@playwright/test";
import { apiDelete, apiGet, expectNoHorizontalOverflow } from "./test-helpers";

const PROCESS_ID = "00000000-0000-0000-0000-000000000301";

test("standalone Diavgeia ADA is searchable and opens its canonical act", async ({ page }) => {
  await page.goto("/?q=6ΙΖ07Λ7-ΕΨΒ");
  await expect(page.getByText("Ακριβής ταύτιση")).toBeVisible();
  await expect(page.getByText("6ΙΖ07Λ7-ΕΨΒ")).toBeVisible();
  await expect(page.getByRole("link", { name: "Άνοιγμα επίσημης εγγραφής" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Άνοιγμα εγγράφου προκήρυξης" })).toBeVisible();
  await page.locator(".result-card-body-link").click();
  await expect(page.getByText("6ΙΖ07Λ7-ΕΨΒ")).toBeVisible();
  await expect(page.getByText("DIAVGEIA_DECISION").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Σύνοψη προκήρυξης" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("link", { name: "Επίσημη σελίδα" }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: "Έγγραφο", exact: true }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: "Διαδικασία 360" })).toHaveCount(0);
  await expectNoHorizontalOverflow(page);
});

test("real KHMDHS notice exposes summary, provider page, PDF, and extracted file", async ({ page }) => {
  await page.goto("/?q=26PROC019308569");
  await expect(page.getByText("26PROC019308569")).toBeVisible();
  await expect(page.getByRole("link", { name: "Άνοιγμα επίσημης εγγραφής" })).toHaveAttribute(
    "href",
    /cerpp\.eprocurement\.gov\.gr/,
  );
  await expect(page.getByRole("link", { name: "Άνοιγμα εγγράφου προκήρυξης" })).toHaveAttribute(
    "href",
    /notice\/attachment\/26PROC019308569$/,
  );
  await page.locator(".result-card-body-link").click();

  await expect(page.getByRole("heading", { name: "Σύνοψη προκήρυξης" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("15.700,00 EUR", { exact: true })).toBeVisible();
  await expect(page.getByText("10/07/2026 10:00", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Documents", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Επίσημες πηγές και αρχεία" })).toBeVisible();
  await expect(page.getByText("69 σελίδες")).toBeVisible();
  await expect(page.getByText("TEXT_LAYER")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("process 360 exposes every intelligence tab and evidence drawer", async ({ page }) => {
  await page.goto(`/processes/${PROCESS_ID}`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Σύνοψη προκήρυξης" })).toBeVisible();

  await page.getByRole("button", { name: "Evidence" }).click();
  const evidence = page.getByRole("dialog", { name: "Τεκμηρίωση δεδομένων" });
  await expect(evidence).toBeVisible();
  await expect(evidence).toContainText(/field references|Δεν υπάρχουν field references/);
  await evidence.getByRole("button", { name: "Κλείσιμο" }).click();

  const tabs = [
    ["Overview", "Αναθέτουσα αρχή"],
    ["Documents", "Πράξεις"],
    ["Bid workspace", /Αξιολόγηση και προετοιμασία προσφοράς|Καθήκοντα ομάδας/],
    ["Buyer history", "Buyer history"],
    ["Competitors", "Ανταγωνιστικό τοπίο"],
    ["Similar contracts", "Similar contracts"],
    ["Lifecycle", "Χρονολόγιο"],
    ["Funding", "Χρηματοδοτούμενα έργα"],
    ["Notes", "Σημειώσεις"],
  ] as const;
  for (const [tab, heading] of tabs) {
    await page.getByRole("button", { name: tab, exact: true }).click();
    await expect(page.getByRole("heading", { name: heading, exact: typeof heading === "string" }).first()).toBeVisible();
  }
  await expectNoHorizontalOverflow(page);
});

test("document intelligence answers against page evidence with citations", async ({ page }) => {
  await page.goto(`/processes/${PROCESS_ID}`);
  await page.getByRole("button", { name: "Documents", exact: true }).click();
  await page.getByLabel("Ερώτηση στα έγγραφα").fill("Ποιες είναι οι βασικές απαιτήσεις;");
  await page.getByRole("button", { name: "Υποβολή ερώτησης" }).click();
  await expect(page.locator(".document-answer")).toBeVisible({ timeout: 20_000 });
  await expect(page.locator(".document-answer")).toContainText(/EXTRACTIVE|LLM GROUNDED|NO EVIDENCE/);
  await expect(page.locator(".document-chat-log")).toHaveCSS("overflow-y", "auto");
  await expectNoHorizontalOverflow(page);
});

test("bid workspace persists decision, tasks, and requirements", async ({ page, request }) => {
  type BidWorkspace = {
    status: string;
    decision: string;
    decision_rationale: string | null;
    submission_due_at: string | null;
    tasks: Array<{ id: string; title: string }>;
    requirements: Array<{ id: string; title: string }>;
  };
  const initialResponse = await request.post(`/api/v1/bids/${PROCESS_ID}`);
  expect([200, 201]).toContain(initialResponse.status());
  const initial = await initialResponse.json() as BidWorkspace;
  const taskTitle = `E2E task ${Date.now()}`;
  const requirementTitle = `E2E requirement ${Date.now()}`;
  let taskId: string | null = null;
  let requirementId: string | null = null;

  try {
    await page.goto(`/processes/${PROCESS_ID}`);
    await page.getByRole("button", { name: "Bid workspace", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Καθήκοντα ομάδας" })).toBeVisible();
    await page.getByLabel("Νέο καθήκον").fill(taskTitle);
    const taskResponse = page.waitForResponse((response) =>
      response.url().includes(`/api/v1/bids/${PROCESS_ID}/tasks`) && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Προσθήκη καθήκοντος" }).click();
    taskId = (await (await taskResponse).json() as { id: string }).id;
    await expect(page.getByText(taskTitle)).toBeVisible();
    await page.getByLabel(`Κατάσταση ${taskTitle}`).selectOption("IN_PROGRESS");

    await page.getByLabel("Τύπος απαίτησης").selectOption("CERTIFICATE");
    await page.getByLabel("Νέα απαίτηση").fill(requirementTitle);
    const requirementResponse = page.waitForResponse((response) =>
      response.url().includes(`/api/v1/bids/${PROCESS_ID}/requirements`) && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Προσθήκη απαίτησης" }).click();
    requirementId = (await (await requirementResponse).json() as { id: string }).id;
    await expect(page.getByLabel("Απαιτήσεις", { exact: true }).getByText(requirementTitle)).toBeVisible();
    await page.getByLabel(`Κατάσταση ${requirementTitle}`).selectOption("MET");

    await page.getByRole("button", { name: "BID", exact: true }).click();
    await expect.poll(async () => (await apiGet<BidWorkspace>(request, `/v1/bids/${PROCESS_ID}`)).decision).toBe("BID");
  } finally {
    if (taskId) await apiDelete(request, `/v1/bids/${PROCESS_ID}/tasks/${taskId}`);
    if (requirementId) await apiDelete(request, `/v1/bids/${PROCESS_ID}/requirements/${requirementId}`);
    await request.patch(`/api/v1/bids/${PROCESS_ID}`, {
      data: {
        status: initial.status,
        decision: initial.decision,
        decision_rationale: initial.decision_rationale,
        submission_due_at: initial.submission_due_at,
      },
    });
  }
});

test("process notes and tags persist and can be removed", async ({ page, request }) => {
  type Note = { id: string; body: string };
  type Tag = { id: string; name: string };
  const noteText = `E2E qualification note ${Date.now()}`;
  const tagName = "e2e-verified";

  try {
    await page.goto(`/processes/${PROCESS_ID}`);
    await page.getByRole("button", { name: "Notes", exact: true }).click();
    await page.getByLabel("Νέα σημείωση").fill(noteText);
    await page.getByRole("button", { name: "Προσθήκη", exact: true }).click();
    const noteRow = page.locator(".notes-list article").filter({ hasText: noteText });
    await expect(noteRow).toBeVisible();

    await page.getByLabel("Νέο tag").fill(tagName);
    await page.getByRole("button", { name: "Προσθήκη tag" }).click();
    await expect(page.getByText(tagName, { exact: true })).toBeVisible();
    await page.getByRole("button", { name: `Αφαίρεση ${tagName}` }).click();
    await expect(page.getByText(tagName, { exact: true })).toHaveCount(0);

    await noteRow.getByRole("button", { name: "Διαγραφή σημείωσης" }).click();
    await expect(noteRow).toHaveCount(0);
  } finally {
    const notes = await apiGet<Note[]>(request, `/v1/workspace/notes?object_type=procurement_processes&object_id=${PROCESS_ID}`);
    for (const note of notes.filter((item) => item.body === noteText)) {
      await apiDelete(request, `/v1/workspace/notes/${note.id}`);
    }
    const linkedTags = await apiGet<Tag[]>(request, `/v1/workspace/tags/links?object_type=procurement_processes&object_id=${PROCESS_ID}`);
    for (const tag of linkedTags.filter((item) => item.name === tagName)) {
      await apiDelete(request, `/v1/workspace/tags/${tag.id}/links/procurement_processes/${PROCESS_ID}`);
    }
  }
});

test("buyer and supplier drill-downs expose intelligence and provenance", async ({ page, request }) => {
  const contractSearch = await apiGet<{ data: Array<{ process_id: string | null }> }>(
    request,
    "/v1/search?q=25SYMV012345678",
  );
  const contractProcessId = contractSearch.data[0]?.process_id;
  expect(contractProcessId).toBeTruthy();
  await page.goto(`/processes/${contractProcessId}`);
  const buyerHref = await page.locator('a[href^="/buyers/"]').first().getAttribute("href");
  const companyHref = await page.locator('a[href^="/companies/"]').first().getAttribute("href");
  expect(buyerHref).toBeTruthy();
  expect(companyHref).toBeTruthy();

  await page.goto(buyerHref!);
  await expect(page.getByText("Buyer", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "CPV κατανομή" })).toBeVisible();
  await page.getByRole("button", { name: "Evidence" }).click();
  await expect(page.getByRole("dialog", { name: "Τεκμηρίωση δεδομένων" })).toBeVisible();
  await page.getByRole("button", { name: "Κλείσιμο" }).click();

  await page.goto(companyHref!);
  await expect(page.getByText("Supplier", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Buyer dependency" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "CPV και γεωγραφική παρουσία" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

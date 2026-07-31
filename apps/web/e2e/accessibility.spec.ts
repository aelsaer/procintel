import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { openSidebarView } from "./test-helpers";

async function expectNoSeriousAccessibilityViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const violations = results.violations
    .filter((violation) => violation.impact === "serious" || violation.impact === "critical")
    .map((violation) => ({
      id: violation.id,
      impact: violation.impact,
      help: violation.help,
      targets: violation.nodes.map((node) => node.target.join(" ")),
    }));
  expect(violations).toEqual([]);
}

test("primary workspaces meet serious WCAG checks", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Δραστηριότητα και μόνιμη στόχευση" })).toBeVisible();
  await expectNoSeriousAccessibilityViolations(page);

  for (const view of ["Ευκαιρίες", "Alerts", "Ανταγωνισμός", "Frameworks", "Analytics", "Αρχείο"]) {
    await openSidebarView(page, view);
    await page.waitForTimeout(300);
    await expectNoSeriousAccessibilityViolations(page);
  }

  await openSidebarView(page, "Analytics");
  await page.getByRole("button", { name: "Ευρώπη" }).click();
  await expect(page.getByRole("heading", { name: "Η αγορά ανά χώρα" })).toBeVisible();
  await expectNoSeriousAccessibilityViolations(page);
});

test("analytics dialogs, map alternative, and detail workspace meet serious WCAG checks", async ({ page }) => {
  await page.goto("/");
  await openSidebarView(page, "Analytics");
  await page.getByRole("button", { name: "Χάρτης" }).click();
  await expect(page.getByTestId("greece-nuts-map")).toBeVisible();
  await expect(page.getByLabel("Κατάταξη περιφερειών")).toBeVisible();
  await expectNoSeriousAccessibilityViolations(page);

  await page.goto("/processes/00000000-0000-0000-0000-000000000301");
  await page.getByRole("button", { name: "Notes", exact: true }).click();
  await expectNoSeriousAccessibilityViolations(page);
  await page.getByRole("button", { name: "Evidence" }).click();
  await expect(page.getByRole("dialog", { name: "Τεκμηρίωση δεδομένων" })).toBeVisible();
  await expectNoSeriousAccessibilityViolations(page);
});

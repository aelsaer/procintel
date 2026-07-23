import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test.use({ storageState: { cookies: [], origins: [] } });

test("workspace requires login and local development session can log out", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login\?to=%2F/);
  await expect(page.getByRole("heading", { name: "Σύνδεση στο workspace" })).toBeVisible();
  const accessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  expect(accessibility.violations.filter((violation) => violation.impact === "serious" || violation.impact === "critical")).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);

  await page.getByRole("button", { name: "Είσοδος στο τοπικό workspace" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: "Τι θέλεις να παρακολουθεί το Procintel;" })).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.localStorage.getItem("procintel_local_session"))).toBe("true");

  await page.getByRole("button", { name: "Αποσύνδεση" }).click();
  await expect(page).toHaveURL(/\/login(?:\?to=%2F)?$/);
  await expect(page.getByRole("heading", { name: "Σύνδεση στο workspace" })).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.localStorage.getItem("procintel_local_session"))).toBeNull();
});

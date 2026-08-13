import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const enabled = process.env.PROCINTEL_KEYCLOAK_E2E === "1";

test.describe("live Keycloak authentication", () => {
  test.skip(!enabled, "Set PROCINTEL_KEYCLOAK_E2E=1 with the local Keycloak stack running.");
  test.use({ storageState: { cookies: [], origins: [] } });

  test("signs in, reaches the tenant workspace, and performs single logout", async ({ page, baseURL }) => {
    const workspacePort = new URL(baseURL ?? "http://127.0.0.1:3000").port;
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "Σύνδεση στο workspace" })).toBeVisible();
    const loginAccessibility = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(
      loginAccessibility.violations.filter(
        (violation) => violation.impact === "serious" || violation.impact === "critical",
      ),
    ).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
    await page.getByRole("button", { name: "Ασφαλής σύνδεση" }).click();

    await expect(page).toHaveURL(/localhost:8080\/realms\/procintel\//);
    await page.locator("#username").fill("demo@procintel.local");
    await page.locator("#password").fill("ProcintelDemo!2026");
    await page.locator("#kc-login").click();

    await expect.poll(() => new URL(page.url()).pathname).toBe("/");
    expect(new URL(page.url()).port).toBe(workspacePort);
    await expect(page.getByRole("button", { name: "Αποσύνδεση" })).toBeVisible();

    await page.locator("a.workspace-account").click();
    await expect(page.getByRole("heading", { name: "Ομάδα και πρόσβαση" })).toBeVisible();
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Αποσύνδεση" })).toBeVisible();

    const identity = await page.evaluate(async () => {
      const response = await fetch("/api/v1/workspace/me");
      return {
        status: response.status,
        body: await response.json(),
        leakedToken: window.localStorage.getItem("procintel_access_token"),
        leakedSession: window.localStorage.getItem("procintel_oidc_session"),
      };
    });
    expect(identity.status).toBe(200);
    expect(identity.leakedToken).toBeNull();
    expect(identity.leakedSession).toBeNull();
    expect(identity.body).toMatchObject({
      email: "demo@procintel.local",
      tenant_id: "00000000-0000-0000-0000-000000000101",
      role: "ANALYST",
    });

    await page.getByRole("button", { name: "Αποσύνδεση" }).click();
    await expect.poll(() => new URL(page.url()).pathname).toBe("/login");
    expect(new URL(page.url()).port).toBe(workspacePort);
    await expect.poll(() => page.evaluate(() => window.localStorage.getItem("procintel_oidc_session"))).toBeNull();
  });

  test("opens the branded registration form and passes accessibility checks", async ({ page }) => {
    await page.goto("/login?view=signup");
    await page.getByRole("textbox", { name: "Επωνυμία επιχείρησης" }).fill("Procintel E2E Supplier");
    await page.getByRole("button", { name: "Δημιουργία λογαριασμού" }).click();

    await expect(page).toHaveURL(/localhost:8080\/realms\/procintel\//);
    await expect(page.locator("#email")).toBeVisible();
    const accessibility = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(
      accessibility.violations.filter(
        (violation) => violation.impact === "serious" || violation.impact === "critical",
      ),
    ).toEqual([]);
  });
});

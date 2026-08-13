import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test.use({ storageState: { cookies: [], origins: [] } });

test("does not expose internal API metrics through the BFF", async ({ request }) => {
  const response = await request.get("/api/metrics");
  expect(response.status()).toBe(404);
});

test("workspace requires login and local development session can log out", async ({ page }) => {
  await page.route("**/runtime-config", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: "{}",
  }));
  await page.route("**/api/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === "/api/v1/workspace/me") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          subject: "local-owner",
          email: "owner@procintel.local",
          tenant_id: "00000000-0000-0000-0000-000000000101",
          tenant_name: "Local workspace",
          plan: "DEVELOPMENT",
          role: "OWNER",
        }),
      });
      return;
    }
    if (pathname === "/api/v1/workspace/login") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ acknowledged: true }),
      });
      return;
    }
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "not required by isolated auth flow" }),
    });
  });

  await page.goto("/");
  await expect(page).toHaveURL(/\/login\?to=%2F/);
  await expect(page.getByRole("heading", { name: "Σύνδεση στο workspace" })).toBeVisible();
  const accessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  expect(accessibility.violations.filter((violation) => violation.impact === "serious" || violation.impact === "critical")).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);

  await page.getByRole("button", { name: "Είσοδος στο τοπικό workspace" }).click();
  await expect.poll(() => page.evaluate(() => window.localStorage.getItem("procintel_local_session"))).toBe("true");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("button", { name: "Αποσύνδεση" })).toBeVisible();

  await page.getByRole("button", { name: "Αποσύνδεση" }).click();
  await expect(page).toHaveURL(/\/login(?:\?to=%2F)?$/);
  await expect(page.getByRole("heading", { name: "Σύνδεση στο workspace" })).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.localStorage.getItem("procintel_local_session"))).toBeNull();
});

test("configured OIDC sign-up delegates to the server-side PKCE flow", async ({ page }) => {
  await page.route("**/runtime-config", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        issuerUrl: "https://identity.example.test/realms/procintel",
        clientId: "procintel-web",
        redirectUri: "http://localhost:3000/callback",
      }),
    });
  });
  await page.route("**/auth/login**", (route) => route.abort());

  await page.goto("/login?view=signup&to=%2Fsettings");
  await expect(page.getByRole("heading", { name: "Δημιουργία λογαριασμού" })).toBeVisible();
  await page.getByLabel("Επωνυμία επιχείρησης").fill("Example Technologies AE");
  expect(await page.evaluate(() => window.localStorage.getItem("procintel_access_token"))).toBeNull();
  const requestPromise = page.waitForRequest(
    (request) => new URL(request.url()).pathname === "/auth/login",
  );
  await page.getByRole("button", { name: "Δημιουργία λογαριασμού" }).click();
  const authorizeUrl = new URL((await requestPromise).url());
  expect(authorizeUrl.searchParams.get("intent")).toBe("signup");
  expect(authorizeUrl.searchParams.get("returnTo")).toBe("/settings");
  expect(authorizeUrl.searchParams.get("organizationName")).toBe("Example Technologies AE");
});

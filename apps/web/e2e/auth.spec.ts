import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test.use({ storageState: { cookies: [], origins: [] } });

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

test("configured OIDC sign-up starts Authorization Code with PKCE", async ({ page }) => {
  await page.route("**/runtime-config", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        issuerUrl: "https://identity.example.test/realms/procintel",
        clientId: "procintel-web",
        redirectUri: "http://127.0.0.1:3000/callback",
      }),
    });
  });
  await page.route("https://identity.example.test/realms/procintel/.well-known/openid-configuration", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: { "Access-Control-Allow-Origin": "*" },
      body: JSON.stringify({
        issuer: "https://identity.example.test/realms/procintel",
        authorization_endpoint: "https://identity.example.test/realms/procintel/protocol/openid-connect/auth",
        token_endpoint: "https://identity.example.test/realms/procintel/protocol/openid-connect/token",
        end_session_endpoint: "https://identity.example.test/realms/procintel/protocol/openid-connect/logout",
      }),
    });
  });
  await page.route("https://identity.example.test/realms/procintel/protocol/openid-connect/auth**", (route) => route.abort());

  await page.goto("/login?view=signup&to=%2Fsettings");
  await expect(page.getByRole("heading", { name: "Δημιουργία λογαριασμού" })).toBeVisible();
  await page.getByLabel("Επωνυμία επιχείρησης").fill("Example Technologies AE");
  const requestPromise = page.waitForRequest(
    (request) => request.url().startsWith("https://identity.example.test/realms/procintel/protocol/openid-connect/auth?"),
  );
  await page.getByRole("button", { name: "Δημιουργία λογαριασμού" }).click();
  const authorizeUrl = new URL((await requestPromise).url());
  expect(authorizeUrl.searchParams.get("client_id")).toBe("procintel-web");
  expect(authorizeUrl.searchParams.get("response_type")).toBe("code");
  expect(authorizeUrl.searchParams.get("prompt")).toBe("create");
  expect(authorizeUrl.searchParams.get("code_challenge_method")).toBe("S256");
  expect(authorizeUrl.searchParams.get("code_challenge")).toBeTruthy();
  expect(authorizeUrl.searchParams.get("state")).toBeTruthy();
  expect(authorizeUrl.searchParams.get("nonce")).toBeTruthy();
});

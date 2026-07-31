import { expect, test } from "@playwright/test";
import { apiDelete, apiGet, openSidebarView } from "./test-helpers";

test("opportunity relevance feedback is persisted and can be removed", async ({ page, request }) => {
  await page.goto("/");
  await openSidebarView(page, "Ευκαιρίες");
  const firstRow = page.locator(".opportunity-card").first();
  await expect(firstRow).toBeVisible();
  const href = await firstRow.locator('a[href^="/processes/"]').getAttribute("href");
  const processId = href?.split("/").pop();
  expect(processId).toBeTruthy();
  const target = { process_id: processId! };
  const row = page.locator(".opportunity-card").filter({
    has: page.locator(`a[href="/processes/${target.process_id}"]`),
  });
  const existing = await apiGet<Array<{ process_id: string; label: string }>>(
    request,
    "/v1/business-profile/relevance-feedback",
  );
  const previous = existing.find((item) => item.process_id === target.process_id);

  try {
    await row.getByRole("button", { name: "Σήμανση ως άσχετη" }).click();
    await expect(row).toHaveCount(0);
    const feedback = await apiGet<Array<{ process_id: string; label: string }>>(
      request,
      "/v1/business-profile/relevance-feedback",
    );
    expect(feedback).toContainEqual(expect.objectContaining({ process_id: target.process_id, label: "IRRELEVANT" }));
  } finally {
    if (previous) {
      await request.put("/api/v1/business-profile/relevance-feedback", {
        data: { process_id: previous.process_id, label: previous.label },
      });
    } else {
      await apiDelete(request, `/v1/business-profile/relevance-feedback/${target.process_id}`);
    }
  }
});

test("workspace admin creates and revokes invitations and API keys", async ({ page, request }) => {
  const unique = Date.now();
  const inviteEmail = `e2e-${unique}@example.test`;
  const keyName = `E2E key ${unique}`;
  let invitationId: string | null = null;
  let keyId: string | null = null;

  try {
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "Ομάδα και πρόσβαση" })).toBeVisible();

    await page.getByLabel("Email πρόσκλησης").fill(inviteEmail);
    await page.getByLabel("Ρόλος πρόσκλησης").selectOption("ANALYST");
    const invitationResponse = page.waitForResponse((response) =>
      response.url().includes("/api/v1/account/invitations") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Πρόσκληση" }).click();
    const invitation = await (await invitationResponse).json() as { id: string; invitation_token: string };
    invitationId = invitation.id;
    expect(invitation.invitation_token).toMatch(/^pi_/);
    await expect(page.getByText(inviteEmail, { exact: true })).toBeVisible();

    await page.getByLabel("Όνομα API key").fill(keyName);
    const keyResponse = page.waitForResponse((response) =>
      response.url().includes("/api/v1/account/api-keys") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Νέο key" }).click();
    const key = await (await keyResponse).json() as { id: string; key: string };
    keyId = key.id;
    expect(key.key).toMatch(/^pk_/);
    await expect(page.locator(".settings-row").filter({ hasText: keyName })).toBeVisible();

    await page.locator(".settings-row").filter({ hasText: keyName }).getByRole("button", { name: "Ανάκληση API key" }).click();
    await expect(page.locator(".settings-row").filter({ hasText: keyName })).toContainText("REVOKED");
    keyId = null;
    await page.locator(".settings-row").filter({ hasText: inviteEmail }).getByRole("button", { name: "Ανάκληση πρόσκλησης" }).click();
    await expect(page.locator(".settings-row").filter({ hasText: inviteEmail })).toContainText("REVOKED");
    invitationId = null;
  } finally {
    if (keyId) await apiDelete(request, `/v1/account/api-keys/${keyId}`);
    if (invitationId) await apiDelete(request, `/v1/account/invitations/${invitationId}`);
  }
});

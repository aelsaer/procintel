import { expect, type APIRequestContext, type Page } from "@playwright/test";

export async function apiGet<T>(request: APIRequestContext, path: string): Promise<T> {
  const response = await request.get(`/api${path}`);
  expect(response.ok(), `${response.status()} GET ${path}: ${await response.text()}`).toBeTruthy();
  return response.json() as Promise<T>;
}

export async function apiDelete(request: APIRequestContext, path: string): Promise<void> {
  const response = await request.delete(`/api${path}`);
  expect([200, 204, 404]).toContain(response.status());
}

export async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
}

export async function openSidebarView(page: Page, name: string): Promise<void> {
  await page.getByRole("navigation").getByRole("button", { name }).click();
}


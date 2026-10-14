import { expect, type Locator, type Page } from "@playwright/test";

export function column(page: Page, title: string): Locator {
  return page.locator(".stream-section").filter({
    has: page.locator(".section-title", { hasText: title }),
  });
}

export async function openApp(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.locator(".brand")).toHaveText("Tg-up");
}

export async function openSettings(page: Page, section?: string): Promise<Locator> {
  await page.locator(".settings-pill-btn").click();
  const dialog = page.getByRole("dialog", { name: "Settings" });
  await expect(dialog).toBeVisible();
  if (section) {
    // 导航按钮上会挂数量角标，可访问名称不是纯标题。
    await dialog.locator("button.nav-tab-btn", { hasText: section }).click();
  }
  return dialog;
}

export function orderSelect(page: Page): Locator {
  return page.locator(".settings-modal select.app-select").filter({
    has: page.locator("option[value='smallest']"),
  });
}

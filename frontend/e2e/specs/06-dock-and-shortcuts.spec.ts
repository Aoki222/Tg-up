import { openApp, openSettings } from "../helpers/ui";
import { expect, test } from "../fixtures";

test("目录坞展示监听根，暂停和恢复全局监控", async ({ page, world }) => {
  await openApp(page);
  const dock = page.locator("aside.dock");
  await expect(dock.getByRole("heading", { name: "Watch" })).toBeVisible();
  await expect(dock).toContainText("inbox");
  await expect(dock).toContainText("D:/media/inbox");
  await expect(dock).toContainText("1 active");

  await dock.getByRole("button", { name: "Watching" }).click();
  await expect(page.locator(".el-message--warning")).toContainText("Global watch paused. Every folder paused with it.");
  await expect(dock).toContainText("Watch paused");
  await expect(dock.getByRole("button", { name: "Paused" })).toBeVisible();
  expect(world.settings.watch_enabled).toBe(false);
  expect(world.settings.observer_roots[0]?.enabled).toBe(false);

  await dock.getByRole("button", { name: "Paused" }).click();
  await expect(page.locator(".el-message--success")).toContainText("Global watch is on. Scanning every folder.");
  await expect(dock).toContainText("1 active");
  expect(world.settings.watch_enabled).toBe(true);
  expect(world.settings.observer_roots[0]?.enabled).toBe(true);
});

test("新加的监听路径默认关闭", async ({ page, world }) => {
  await openApp(page);
  await expect(page.locator("aside.dock")).toContainText("inbox");
  await page.locator("aside.dock").getByRole("button", { name: "+ Add folder" }).click();
  const box = page.getByRole("dialog", { name: "Add watch folder" });
  await box.locator("input").fill("   ");
  await box.getByRole("button", { name: "Add", exact: true }).click();
  await expect(box).toContainText("Path is required");

  await box.locator("input").fill("D:/media/inbox");
  await box.getByRole("button", { name: "Add", exact: true }).click();
  await expect(box).toContainText("That path is already watched");

  await box.locator("input").fill("D:/media/new-show");
  await box.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.locator(".el-message--success")).toContainText('Added "new-show"');
  const card = page.locator(".dock-item").filter({ hasText: "new-show" });
  await expect(card).toBeVisible();
  await expect(card).toHaveClass(/is-root-paused/);

  const saved = world.settingsPuts.at(-1);
  const added = saved?.observer_roots.find((item) => item.path === "D:/media/new-show");
  expect(added?.enabled).toBe(false);
  expect(saved?.revision).toBe(42);
});

test("删除监听路径后列表清空并暂停", async ({ page, world }) => {
  await openApp(page);
  await expect(page.locator("aside.dock")).toContainText("inbox");
  await page.locator("aside.dock").getByRole("button", { name: "Delete" }).click();
  await page.getByRole("button", { name: "Confirm delete" }).click();
  await expect(page.locator(".el-message--success")).toContainText('Removed "inbox"');
  await expect(page.locator(".dock-empty")).toHaveText("No folders yet. Add one below.");
  await expect(page.locator("aside.dock")).toContainText("Watch paused");
  expect(world.settings.observer_roots).toEqual([]);
  expect(world.settings.watch_enabled).toBe(false);
});

test("目录策略可以单独保存落稳等待", async ({ page, world }) => {
  await openApp(page);
  await page.locator("aside.dock").getByRole("button", { name: "Route" }).click();
  const policy = page.getByRole("dialog", { name: /Delivery route/ });
  const delay = policy.locator("#settle-delay");
  await expect(delay).toBeVisible();
  await expect(delay).toHaveValue("0");
  await policy.locator("select.target-select").selectOption("tg:-100123");
  await delay.fill("180");
  await policy.getByRole("button", { name: "Save route" }).click();
  await expect(page.locator(".el-message--success")).toContainText('Saved "inbox"');
  const route = world.settingsPuts.at(-1)?.routes[0] as {
    path: string;
    settle_delay_seconds: number;
    dest_id: string;
  };
  expect(route.path).toBe("D:/media/inbox");
  expect(route.settle_delay_seconds).toBe(180);
  expect(route.dest_id).toBe("-100123");
});

test("策略弹层和空的手动投递", async ({ page }) => {
  await openApp(page);
  await expect(page.locator("aside.dock")).toContainText("inbox");
  await page.locator("aside.dock").getByRole("button", { name: "Route" }).click();
  const policy = page.getByRole("dialog", { name: /Delivery route/ });
  await expect(policy).toContainText("D:/media/inbox");
  await page.keyboard.press("Escape");
  await expect(policy).toBeHidden();

  await page.locator("aside.dock").getByRole("button", { name: "Send files" }).click();
  const dispatch = page.getByRole("dialog", { name: "Send files" });
  await expect(dispatch).toBeVisible();
  await expect(dispatch).toContainText("inbox");
  await expect(dispatch).toContainText("0 files selected");
  await expect(dispatch).toContainText("Select files in the folder tree.");
  await expect(dispatch.getByRole("button", { name: /Send now/ })).toBeDisabled();
});

test("快捷键开关目录、历史和设置", async ({ page }) => {
  await openApp(page);

  await page.locator(".sidebar-toggle-btn").click();
  await expect(page.locator("aside.dock")).toBeHidden();
  await page.keyboard.press("Control+b");
  await expect(page.getByRole("heading", { name: "Watch" })).toBeVisible();

  await page.keyboard.press("Control+h");
  const history = page.getByRole("dialog", { name: "Transfer history" });
  await expect(history).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(history).toBeHidden();

  await page.keyboard.press("Control+Comma");
  const settings = page.getByRole("dialog", { name: "Settings" });
  await expect(settings).toBeVisible();
  await expect(page).toHaveURL(/settings=telegram/);
  await page.keyboard.press("Escape");
  await expect(settings).toBeHidden();
});

test("设置层打开时 Esc 先关掉设置", async ({ page }) => {
  await openApp(page);
  await openSettings(page, "Watch folders");
  await expect(page).toHaveURL(/settings=watch/);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Settings" })).toBeHidden();
  await expect(page).not.toHaveURL(/settings=/);
});

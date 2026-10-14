import { openApp } from "../helpers/ui";
import { worker } from "../helpers/world";
import { expect, test } from "../fixtures";

test("顶栏在英文和中文之间切换", async ({ page }) => {
  await openApp(page);
  await expect.poll(() => page.evaluate(() => localStorage.getItem("uploader_lang"))).toBeNull();
  await expect(page.locator(".settings-pill-btn")).toContainText("Settings");

  await page.getByRole("button", { name: "中文", exact: true }).click();
  await expect(page.locator(".settings-pill-btn")).toContainText("设置");
  await expect.poll(() => page.evaluate(() => localStorage.getItem("uploader_lang"))).toBe("zh");

  await page.getByRole("button", { name: "EN", exact: true }).click();
  await expect(page.locator(".settings-pill-btn")).toContainText("Settings");
  await expect.poll(() => page.evaluate(() => localStorage.getItem("uploader_lang"))).toBe("en");
});

test("版本药丸打开测试版说明", async ({ page }) => {
  await openApp(page);
  await expect(page.locator(".version-pill")).toHaveText("Staging 2.4.1");
  await page.locator(".version-pill").click();
  const box = page.locator(".el-message-box");
  await expect(box).toContainText("staging branch");
  await expect(box).toContainText("测试提交");
  await box.getByRole("button", { name: "OK" }).click();
  await expect(box).toBeHidden();
});

test("Worker 计数和禁用", async ({ page, world }) => {
  world.workers = [
    worker({ name: "worker_1" }),
    worker({ name: "worker_2", running: false, accepting: false }),
  ];
  world.sessions = {
    items: ["worker_1", "worker_2"],
    accounts: [
      { name: "worker_1", kind: "user", username: "alice" },
      { name: "worker_2", kind: "bot", username: "" },
    ],
    default_group_id: null,
    api_configured: true,
  };

  await openApp(page);
  await expect(page.locator(".worker-chip-count")).toHaveText("1/2");
  await page.locator(".worker-chip-btn").click();

  const dialog = page.getByRole("dialog", { name: "Workers" });
  await expect(dialog).toContainText("Online 1 / 2");
  await expect(dialog.locator(".worker-name", { hasText: "worker_1" })).toBeVisible();
  await expect(dialog.getByText("Idle", { exact: true })).toBeVisible();
  await expect(dialog.getByText("Hold", { exact: true })).toBeVisible();
  await expect(dialog.getByText("User", { exact: true })).toBeVisible();

  await dialog.locator(".item").filter({ hasText: "worker_1" }).getByRole("button", { name: "Disable" }).click();
  await expect(page.locator(".el-message--success")).toContainText("Disabled worker_1");
  await expect(dialog.locator(".item").filter({ hasText: "worker_1" }).locator(".status-label")).toHaveText("Disabled");
  await expect(page.locator(".worker-chip-count")).toHaveText("0/2");
});

test("未配置凭据时显示引导，未命中文件显示横幅", async ({ page, world }) => {
  world.identity = { api_id: 0, api_hash_masked: "", configured: false };
  world.unmatched = [
    {
      id: 1,
      file_path: "D:/media/inbox/a.mp4",
      file_name: "a.mp4",
      folder_name: "inbox",
      file_size: 10,
      discovered_at: null,
    },
    {
      id: 2,
      file_path: "D:/media/inbox/b.mkv",
      file_name: "b.mkv",
      folder_name: "inbox",
      file_size: 20,
      discovered_at: null,
    },
  ];

  await openApp(page);
  const banner = page.locator(".setup-banner");
  await expect(banner).toContainText("API_ID / API_HASH");
  await expect(page.locator(".unmatched-banner")).toHaveText(
    "2 files matched no route and were not queued. Set a chat under Route on the folder.",
  );
  await banner.getByRole("button", { name: "Set up" }).click();
  await expect(page.getByRole("dialog", { name: "Settings" })).toBeVisible();
  await expect(page).toHaveURL(/settings=account/);
});

test("传输历史按今日和累计切换，并能过滤", async ({ page, world }) => {
  world.successToday = 1;
  world.successTodayItems = [
    {
      id: 1,
      file_name: "today.mp4",
      file_size: 2048,
      folder_name: "inbox",
      finished_at: "10:00",
      part_label: null,
    },
  ];
  world.successAllItems = [
    ...world.successTodayItems,
    {
      id: 2,
      file_name: "older.mkv",
      file_size: 4096,
      folder_name: "inbox",
      finished_at: "昨天",
      part_label: "第 1/2 段",
    },
  ];

  await openApp(page);
  await expect(page.locator(".scope-island-label")).toHaveText("Today:");
  await expect(page.locator(".scope-island-count")).toHaveText("1");
  await page.locator(".scope-island-toggle-btn").click();
  await expect(page.locator(".scope-island-label")).toHaveText("All time:");
  await expect(page.locator(".scope-island-count")).toHaveText("2");

  await page.locator(".scope-island-view-btn").click();
  const history = page.getByRole("dialog", { name: "Transfer history" });
  await expect(history).toContainText("older.mkv");
  await expect(history).toContainText("第 1/2 段");
  await expect(history).toContainText("Delivered");

  await history.getByRole("button", { name: "Today", exact: true }).click();
  await expect(history).toContainText("today.mp4");
  await expect(history.getByText("older.mkv")).toHaveCount(0);

  await history.locator(".success-search-input").fill("没有这个文件");
  await expect(history).toContainText("No matching transfers");
  await history.locator(".success-search-input").fill("today");
  await expect(history).toContainText("today.mp4");
});

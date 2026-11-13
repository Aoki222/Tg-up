import { openApp } from "../helpers/ui";
import { task } from "../helpers/world";
import { expect, test } from "../fixtures";

test("手机宽度下列表自己滚动，顶栏和底栏不动", async ({ page, world }) => {
  world.board = Array.from({ length: 24 }, (_, index) =>
    task({ id: index + 1, file_name: `clip-${index + 1}.mp4`, status: "pending" }),
  );
  world.failed = [
    task({
      id: 900,
      file_name: "broken.mkv",
      status: "failed",
      error: "send failed",
      message: "send failed",
    }),
  ];
  world.oversized = [
    task({
      id: 901,
      file_name: "huge.mp4",
      status: "oversized",
      file_size: 3_000_000_000,
      sliceable: true,
      slice_phase: "idle",
    }),
  ];
  world.successAllItems = Array.from({ length: 16 }, (_, index) => ({
    id: index + 1,
    file_name: `done-${index + 1}.mp4`,
    file_size: 2048,
    folder_name: "inbox",
    finished_at: "2026-11-01 12:00",
    part_label: null,
  }));
  world.settings = {
    ...world.settings,
    observer_roots: Array.from({ length: 8 }, (_, index) => ({
      path: `D:/media/inbox-${index + 1}`,
      enabled: true,
      ok: true,
      error: "",
    })),
  };

  await page.setViewportSize({ width: 390, height: 844 });
  await openApp(page);
  await expect(page.locator(".mobile-tabbar")).toBeVisible();
  await expect(page.locator(".stream-scroll .task-card").first()).toBeVisible();

  const locked = await page.evaluate(() => {
    const scrolling = document.scrollingElement;
    return {
      body: getComputedStyle(document.body).overflowY,
      html: getComputedStyle(document.documentElement).overflowY,
      scrollHeight: scrolling?.scrollHeight ?? 0,
      clientHeight: scrolling?.clientHeight ?? 0,
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    };
  });
  expect(locked.body).toBe("hidden");
  expect(locked.html).toBe("hidden");
  expect(locked.scrollHeight).toBeLessThanOrEqual(locked.clientHeight + 1);
  expect(locked.scrollWidth).toBeLessThanOrEqual(locked.clientWidth + 1);

  const scroller = page.locator(".stream-scroll");
  const before = await scroller.evaluate((el) => ({
    scrollHeight: el.scrollHeight,
    clientHeight: el.clientHeight,
  }));
  expect(before.scrollHeight).toBeGreaterThan(before.clientHeight + 40);

  const header = await page.locator(".work-header").boundingBox();
  const tabbar = await page.locator(".mobile-tabbar").boundingBox();
  expect(header).toBeTruthy();
  expect(tabbar).toBeTruthy();
  await scroller.evaluate((el) => {
    el.scrollTop = 320;
  });
  await expect.poll(() => scroller.evaluate((el) => el.scrollTop)).toBeGreaterThan(200);
  const headerAfter = await page.locator(".work-header").boundingBox();
  const tabbarAfter = await page.locator(".mobile-tabbar").boundingBox();
  expect(headerAfter?.y).toBe(header?.y);
  expect(tabbarAfter?.y).toBe(tabbar?.y);
  const kept = await scroller.evaluate((el) => el.scrollTop);

  await page.getByRole("button", { name: "Folders", exact: true }).click();
  const dock = page.locator(".dock:not(.shut)");
  await expect(dock).toBeVisible();
  await expect.poll(() => dock.evaluate((el) => getComputedStyle(el).position)).not.toBe("fixed");
  await dock.getByRole("button", { name: "Route" }).first().click();
  const policy = page.locator(".policy-modal");
  await expect(policy).toBeVisible();
  const policyBox = await policy.boundingBox();
  expect(policyBox?.x).toBeLessThan(2);
  expect(policyBox?.width).toBeGreaterThan(370);
  await expect.poll(() => page.locator(".policy-body").evaluate((el) => getComputedStyle(el).overflowY)).toBe("auto");
  await page.locator(".policy-modal .close-btn").click();
  await expect(policy).toBeHidden();

  await page.getByRole("button", { name: "Accounts", exact: true }).click();
  await expect(page.locator(".phone-panel-head").getByRole("heading", { name: "Workers", exact: true })).toBeVisible();
  await expect.poll(() => page.locator(".phone-panel-body").evaluate((el) => getComputedStyle(el).overflowY)).toBe("auto");

  await page.getByRole("button", { name: "More", exact: true }).click();
  await page.locator(".phone-more-list").getByRole("button", { name: "Settings" }).click();
  const settings = page.locator(".settings-modal");
  await expect(settings).toBeVisible();
  const settingsPane = settings.locator(".settings-content-pane");
  await expect.poll(() => settingsPane.evaluate((el) => getComputedStyle(el).overflowY)).toBe("auto");
  const settingsBox = await settingsPane.evaluate((el) => ({
    clientHeight: el.clientHeight,
    top: el.getBoundingClientRect().top,
    bottom: el.getBoundingClientRect().bottom,
  }));
  expect(settingsBox.clientHeight).toBeGreaterThan(200);
  expect(settingsBox.bottom).toBeLessThanOrEqual(844);
  await settings.locator(".modal-close-btn").click();

  await page.locator(".phone-more-list").getByRole("button", { name: "Transfer history" }).click();
  const history = page.locator(".success-modal");
  await expect(history).toBeVisible();
  await history.getByRole("button", { name: "All", exact: true }).click();
  await expect(history.locator(".file-name").first()).toBeVisible();
  await expect.poll(() => history.locator(".success-table thead").evaluate((el) => getComputedStyle(el).display)).toBe("none");
  await expect.poll(() => history.locator(".success-body").evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);
  await history.locator(".success-body").evaluate((el) => {
    el.scrollTop = el.scrollHeight;
  });
  await history.locator(".modal-close-btn").click();

  await page.getByRole("button", { name: "Tasks", exact: true }).click();
  await expect.poll(() => scroller.evaluate((el) => el.scrollTop)).toBe(kept);

  await page.getByRole("button", { name: /^Failed/ }).click();
  const failedCard = page.locator(".task-card.failed");
  await expect(failedCard.getByRole("button", { name: "Retry" })).toBeVisible();
  await expect(failedCard.getByRole("button", { name: "Clear" })).toBeHidden();
  await failedCard.getByRole("button", { name: "More actions" }).click();
  await expect(failedCard.getByRole("button", { name: "Clear" })).toBeVisible();
  const actions = await page.locator(".tabs-actions").boundingBox();
  const scrollBox = await scroller.boundingBox();
  const tabBox = await page.locator(".mobile-tabbar").boundingBox();
  expect(actions && scrollBox && tabBox).toBeTruthy();
  expect(actions!.y).toBeGreaterThanOrEqual(scrollBox!.y + scrollBox!.height - 2);
  expect(actions!.y + actions!.height).toBeLessThanOrEqual(tabBox!.y + 1);

  await page.getByRole("button", { name: "Version and language" }).click();
  await page.getByRole("button", { name: "中文", exact: true }).click();
  await expect(page.locator(".mobile-tabbar").getByRole("button", { name: "任务" })).toBeVisible();
  await page.locator(".header-more-backdrop").click();
  await page.getByRole("button", { name: "全部看板" }).click();

  await page.setViewportSize({ width: 360, height: 800 });
  await expect(page.locator(".mobile-tabbar")).toBeVisible();
  const narrow = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
    overflow: getComputedStyle(document.body).overflowY,
  }));
  expect(narrow.overflow).toBe("hidden");
  expect(narrow.scrollWidth).toBeLessThanOrEqual(narrow.clientWidth + 1);
  await expect.poll(() => scroller.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);
});

test("桌面宽度仍是左右工作台", async ({ page, world }) => {
  world.board = Array.from({ length: 18 }, (_, index) =>
    task({ id: index + 1, file_name: `clip-${index + 1}.mp4`, status: "pending" }),
  );
  world.failed = [task({ id: 900, file_name: "broken.mkv", status: "failed", error: "send failed" })];

  await page.setViewportSize({ width: 1440, height: 900 });
  await openApp(page);

  await expect(page.locator(".mobile-tabbar")).toHaveCount(0);
  await expect(page.locator(".settings-pill-btn")).toBeVisible();
  await expect(page.locator(".header-more-btn")).toBeHidden();
  await expect(page.locator(".dock-resizer")).toBeVisible();

  const brand = await page.locator(".brand").boundingBox();
  const settings = await page.locator(".settings-pill-btn").boundingBox();
  expect(brand && settings).toBeTruthy();
  expect(Math.abs(brand!.y - settings!.y)).toBeLessThan(12);
  expect((await page.locator(".work-header").boundingBox())!.height).toBeLessThan(80);

  const pageLock = await page.evaluate(() => {
    const scrolling = document.scrollingElement;
    return {
      overflow: getComputedStyle(document.body).overflowY,
      scrollHeight: scrolling?.scrollHeight ?? 0,
      clientHeight: scrolling?.clientHeight ?? 0,
    };
  });
  expect(pageLock.overflow).toBe("hidden");
  expect(pageLock.scrollHeight).toBeLessThanOrEqual(pageLock.clientHeight + 1);

  const scroller = page.locator(".stream-scroll");
  await expect.poll(() => scroller.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);
  const header = await page.locator(".work-header").boundingBox();
  await scroller.evaluate((el) => {
    el.scrollTop = 240;
  });
  const headerAfter = await page.locator(".work-header").boundingBox();
  expect(headerAfter?.y).toBe(header?.y);

  await page.getByRole("button", { name: /^Failed/ }).click();
  await expect(page.locator(".task-card.failed").getByRole("button", { name: "Clear" })).toBeVisible();
  await expect(page.locator(".task-card.failed").getByRole("button", { name: "More actions" })).toBeHidden();
});

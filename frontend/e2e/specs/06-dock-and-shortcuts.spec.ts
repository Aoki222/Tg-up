import { openApp, openSettings } from "../helpers/ui";
import { expect, test } from "../fixtures";

test("目录坞展示监听根，暂停和恢复全局监控", async ({ page, world }) => {
  await openApp(page);
  const dock = page.locator("aside.dock");
  await expect(dock.getByRole("heading", { name: "监控目录" })).toBeVisible();
  await expect(dock).toContainText("inbox");
  await expect(dock).toContainText("D:/media/inbox");
  await expect(dock).toContainText("1 处活跃");

  await dock.getByRole("button", { name: "全局监控中" }).click();
  await expect(page.locator(".el-message--warning")).toContainText("已暂停全局自动监控，各目录已联动暂停");
  await expect(dock).toContainText("全局已暂停");
  await expect(dock.getByRole("button", { name: "监控已暂停" })).toBeVisible();
  expect(world.settings.watch_enabled).toBe(false);
  expect(world.settings.observer_roots[0]?.enabled).toBe(false);

  await dock.getByRole("button", { name: "监控已暂停" }).click();
  await expect(page.locator(".el-message--success")).toContainText("已开启全局自动监控，正在全量补扫各目录");
  await expect(dock).toContainText("1 处活跃");
  expect(world.settings.watch_enabled).toBe(true);
  expect(world.settings.observer_roots[0]?.enabled).toBe(true);
});

test("新加的监听路径默认关闭", async ({ page, world }) => {
  await openApp(page);
  await expect(page.locator("aside.dock")).toContainText("inbox");
  await page.locator("aside.dock").getByRole("button", { name: "+ 添加监控路径" }).click();
  const box = page.getByRole("dialog", { name: "添加监控路径" });
  await box.locator("input").fill("   ");
  await box.getByRole("button", { name: "添加", exact: true }).click();
  await expect(box).toContainText("路径不能为空");

  await box.locator("input").fill("D:/media/inbox");
  await box.getByRole("button", { name: "添加", exact: true }).click();
  await expect(box).toContainText("该路径已在监控列表中");

  await box.locator("input").fill("D:/media/new-show");
  await box.getByRole("button", { name: "添加", exact: true }).click();
  await expect(page.locator(".el-message--success")).toContainText("已添加「new-show」，监控关闭");
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
  await page.locator("aside.dock").getByRole("button", { name: "删除" }).click();
  await page.getByRole("button", { name: "确认删除" }).click();
  await expect(page.locator(".el-message--success")).toContainText("已删除监控路径「inbox」");
  await expect(page.locator(".dock-empty")).toHaveText("还没有监听目录，请点击下方添加");
  await expect(page.locator("aside.dock")).toContainText("全局已暂停");
  expect(world.settings.observer_roots).toEqual([]);
  expect(world.settings.watch_enabled).toBe(false);
});

test("目录策略可以单独保存落稳等待", async ({ page, world }) => {
  await openApp(page);
  await page.locator("aside.dock").getByRole("button", { name: "策略" }).click();
  const policy = page.getByRole("dialog", { name: /路径交付策略/ });
  const delay = policy.locator("#settle-delay");
  await expect(delay).toBeVisible();
  await expect(delay).toHaveValue("0");
  await policy.locator("select.target-select").selectOption("tg:-100123");
  await delay.fill("180");
  await policy.getByRole("button", { name: "保存此策略" }).click();
  await expect(page.locator(".el-message--success")).toContainText("已保存「inbox」");
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
  await page.locator("aside.dock").getByRole("button", { name: "策略" }).click();
  const policy = page.getByRole("dialog", { name: /路径交付策略/ });
  await expect(policy).toContainText("D:/media/inbox");
  await page.keyboard.press("Escape");
  await expect(policy).toBeHidden();

  await page.locator("aside.dock").getByRole("button", { name: /手动投递任务/ }).click();
  const dispatch = page.getByRole("dialog", { name: "手动投递任务" });
  await expect(dispatch).toBeVisible();
  await expect(dispatch).toContainText("暂无数据");
  await expect(dispatch).toContainText("已选 0 个文件");
  await expect(dispatch).toContainText("请在左侧目录树中勾选想要投递上传的文件");
  await expect(dispatch.getByRole("button", { name: /立即投递/ })).toBeDisabled();
});

test("快捷键开关目录、历史和设置", async ({ page }) => {
  await openApp(page);

  await page.locator(".sidebar-toggle-btn").click();
  await expect(page.locator("aside.dock")).toBeHidden();
  await page.keyboard.press("Control+b");
  await expect(page.getByRole("heading", { name: "监控目录" })).toBeVisible();

  await page.keyboard.press("Control+h");
  const history = page.getByRole("dialog", { name: "传输历史记录" });
  await expect(history).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(history).toBeHidden();

  await page.keyboard.press("Control+Comma");
  const settings = page.getByRole("dialog", { name: "系统配置" });
  await expect(settings).toBeVisible();
  await expect(page).toHaveURL(/settings=telegram/);
  await page.keyboard.press("Escape");
  await expect(settings).toBeHidden();
});

test("设置层打开时 Esc 先关掉设置", async ({ page }) => {
  await openApp(page);
  await openSettings(page, "监听目录");
  await expect(page).toHaveURL(/settings=watch/);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "系统配置" })).toBeHidden();
  await expect(page).not.toHaveURL(/settings=/);
});

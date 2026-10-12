import { openApp, openSettings, orderSelect } from "../helpers/ui";
import { expect, test } from "../fixtures";

test("保存携带原 revision，并提示已写入", async ({ page, world }) => {
  await openApp(page);
  const dialog = await openSettings(page, "处理与收尾");
  const save = dialog.locator(".save-toml-btn");
  await expect(orderSelect(page)).toHaveValue("oldest");
  await expect(save).toBeDisabled();
  await expect(dialog.getByText("有未保存更改")).toHaveCount(0);

  await orderSelect(page).selectOption("smallest");
  const retries = dialog.locator(".table-row-item").filter({ hasText: "最大重试次数" }).locator("input");
  await retries.fill("5");

  await expect(dialog.getByText("有未保存更改")).toBeVisible();
  await expect(page.locator(".dirty-dot")).toHaveAttribute("aria-label", "有未保存的修改");
  await expect(save).toBeEnabled();
  await save.click();

  await expect(page.locator(".el-message--success")).toContainText("已写入 upload.toml，配置立即生效");
  await expect(dialog.getByText("有未保存更改")).toHaveCount(0);
  await expect(save).toBeDisabled();

  expect(world.settingsPuts).toHaveLength(1);
  const payload = world.settingsPuts[0];
  expect(payload.revision).toBe(42);
  expect(payload.schedule_order).toBe("smallest");
  expect(payload.max_retries).toBe(5);
  expect(payload.chat_id).toBe(-100123);
  expect(world.settings.revision).toBe(43);
});

test("409 显示冲突提示并保留未保存状态", async ({ page, world }) => {
  await openApp(page);
  const dialog = await openSettings(page, "处理与收尾");
  await expect(orderSelect(page)).toHaveValue("oldest");
  await orderSelect(page).selectOption("natural");
  world.settingsStatus = 409;

  await dialog.locator(".save-toml-btn").click();

  await expect(page.locator(".el-message--error")).toContainText("配置已在别处修改，请重新加载后再保存");
  await expect(dialog.getByText("有未保存更改")).toBeVisible();
  await expect(dialog.locator(".save-toml-btn")).toBeEnabled();
  expect(world.settingsPuts[0]?.revision).toBe(42);
  expect(world.settingsPuts[0]?.schedule_order).toBe("natural");
  expect(world.settings.revision).toBe(42);
});

test("上传顺序四个选项和对应说明", async ({ page }) => {
  await openApp(page);
  const dialog = await openSettings(page, "处理与收尾");
  const select = orderSelect(page);
  await expect(select).toHaveValue("oldest");
  await expect(select.locator("option")).toHaveText(["入库顺序", "自然排序", "创建时间", "文件大小"]);

  await select.selectOption("natural");
  await expect(dialog.getByText("按路径和文件名的自然顺序上传，EP2 排在 EP10 前面。")).toBeVisible();
  await select.selectOption("created");
  await expect(dialog.getByText("文件创建时间早的先传。时间相同则小文件先传。")).toBeVisible();
  await select.selectOption("smallest");
  await expect(dialog.getByText("小文件先传。体积相同则创建时间早的先传。")).toBeVisible();
  await select.selectOption("oldest");
  await expect(dialog.getByText("按任务写入队列的先后上传。")).toBeVisible();
});

test("设置搜索、入口和凭据", async ({ page, world }) => {
  await openApp(page);
  await page.goto("/settings");
  const dialog = page.getByRole("dialog", { name: "系统配置" });
  await expect(dialog).toBeVisible();
  await expect(page).toHaveURL(/settings=telegram/);
  await expect(dialog.getByRole("button", { name: "Google Drive" })).toHaveCount(0);

  await dialog.getByPlaceholder("搜索配置项...").fill("重试");
  await expect(dialog.getByRole("button", { name: "处理与收尾" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "账号与凭据" })).toHaveCount(0);

  await dialog.getByPlaceholder("搜索配置项...").fill("");
  await dialog.getByRole("button", { name: "账号与凭据", exact: true }).click();

  const apiId = dialog.locator(".form-group").filter({ hasText: "API_ID" }).locator("input");
  await expect(apiId).toHaveValue("123456");
  await apiId.fill("0");
  await dialog.getByRole("button", { name: "保存凭据" }).click();
  await expect(page.locator(".el-message--error")).toContainText("API_ID 必须是正整数");

  await apiId.fill("99");
  await dialog.locator(".form-group").filter({ hasText: "API_HASH" }).locator("input").fill("abcdef");
  await dialog.getByRole("button", { name: "保存凭据" }).click();
  await expect(page.locator(".el-message--success")).toContainText("凭据已保存，当前进程已立即生效");
  expect(world.identityPuts.at(-1)).toEqual({ api_id: 99, api_hash: "abcdef" });

  await dialog.getByPlaceholder("输入 API Token").fill("secret-token");
  await dialog.getByRole("button", { name: "保存 Token" }).click();
  await expect(page.locator(".el-message--success").last()).toContainText("Token 已保存，后续请求将自动携带");
  await expect.poll(() => page.evaluate(() => localStorage.getItem("uploader_api_token"))).toBe("secret-token");
});

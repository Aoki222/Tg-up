import { openApp, openSettings, orderSelect } from "../helpers/ui";
import { expect, test } from "../fixtures";

test("保存携带原 revision，并提示已写入", async ({ page, world }) => {
  await openApp(page);
  const dialog = await openSettings(page, "Processing");
  const save = dialog.locator(".save-toml-btn");
  await expect(orderSelect(page)).toHaveValue("oldest");
  await expect(save).toBeDisabled();
  await expect(dialog.getByText("Unsaved changes")).toHaveCount(0);

  await orderSelect(page).selectOption("smallest");
  const retries = dialog.locator(".table-row-item").filter({ hasText: "Max retries" }).locator("input");
  await retries.fill("5");

  await expect(dialog.getByText("Unsaved changes")).toBeVisible();
  await expect(page.locator(".dirty-dot")).toHaveAttribute("aria-label", "Unsaved changes");
  await expect(save).toBeEnabled();
  await save.click();

  await expect(page.locator(".el-message--success")).toContainText("Saved to upload.toml. It applies now.");
  await expect(dialog.getByText("Unsaved changes")).toHaveCount(0);
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
  const dialog = await openSettings(page, "Processing");
  await expect(orderSelect(page)).toHaveValue("oldest");
  await orderSelect(page).selectOption("natural");
  world.settingsStatus = 409;

  await dialog.locator(".save-toml-btn").click();

  await expect(page.locator(".el-message--error")).toContainText("配置已在别处修改，请重新加载后再保存");
  await expect(dialog.getByText("Unsaved changes")).toBeVisible();
  await expect(dialog.locator(".save-toml-btn")).toBeEnabled();
  expect(world.settingsPuts[0]?.revision).toBe(42);
  expect(world.settingsPuts[0]?.schedule_order).toBe("natural");
  expect(world.settings.revision).toBe(42);
});

test("上传顺序四个选项和对应说明", async ({ page }) => {
  await openApp(page);
  const dialog = await openSettings(page, "Processing");
  const select = orderSelect(page);
  await expect(select).toHaveValue("oldest");
  await expect(select.locator("option")).toHaveText(["Queue order", "Natural", "Created", "Size"]);

  await select.selectOption("natural");
  await expect(dialog.getByText("Natural order of the path and file name. EP2 goes before EP10.")).toBeVisible();
  await select.selectOption("created");
  await expect(dialog.getByText("Earlier file creation time first. Same time, smaller file first.")).toBeVisible();
  await select.selectOption("smallest");
  await expect(dialog.getByText("Smaller files first. Same size, earlier creation time first.")).toBeVisible();
  await select.selectOption("oldest");
  await expect(dialog.getByText("Upload in the order tasks joined the queue.")).toBeVisible();
});

test("设置搜索、入口和凭据", async ({ page, world }) => {
  await openApp(page);
  await page.goto("/settings");
  const dialog = page.getByRole("dialog", { name: "Settings" });
  await expect(dialog).toBeVisible();
  await expect(page).toHaveURL(/settings=telegram/);
  await expect(dialog.getByRole("button", { name: "Google Drive" })).toHaveCount(0);

  await dialog.getByPlaceholder("Search settings...").fill("重试");
  await expect(dialog.getByRole("button", { name: "Processing" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Accounts" })).toHaveCount(0);

  await dialog.getByPlaceholder("Search settings...").fill("");
  await dialog.getByRole("button", { name: "Accounts", exact: true }).click();

  const apiId = dialog.locator(".form-group").filter({ hasText: "API_ID" }).locator("input");
  await expect(apiId).toHaveValue("123456");
  await apiId.fill("0");
  await dialog.getByRole("button", { name: "Save credentials" }).click();
  await expect(page.locator(".el-message--error")).toContainText("API_ID must be a positive integer");

  await apiId.fill("99");
  await dialog.locator(".form-group").filter({ hasText: "API_HASH" }).locator("input").fill("abcdef");
  await dialog.getByRole("button", { name: "Save credentials" }).click();
  await expect(page.locator(".el-message--success")).toContainText("Credentials saved. This process is using them now.");
  expect(world.identityPuts.at(-1)).toEqual({ api_id: 99, api_hash: "abcdef" });

  await dialog.getByPlaceholder("API token").fill("secret-token");
  await dialog.getByRole("button", { name: "Save token" }).click();
  await expect(page.locator(".el-message--success").last()).toContainText("Token saved. Later requests will send it.");
  await expect.poll(() => page.evaluate(() => localStorage.getItem("uploader_api_token"))).toBe("secret-token");
});

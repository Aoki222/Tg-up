import { column, openApp } from "../helpers/ui";
import { task } from "../helpers/world";
import { expect, test } from "../fixtures";

function failedTask(id: number, fileName: string) {
  return task({
    id,
    file_name: fileName,
    status: "failed",
    error: "磁盘已满",
  });
}

test("单条重试后在下一次刷新进入排队", async ({ page, world }) => {
  world.failed = [failedTask(7, "retry-me.mp4")];
  await openApp(page);
  await column(page, "失败与重试看板").getByRole("button", { name: "重试", exact: true }).click();

  await expect(page.locator(".el-message--success")).toContainText("已重新排队");
  await expect(column(page, "排队就绪看板")).toContainText("retry-me.mp4");
  await expect(page.locator(".section-title", { hasText: "失败与重试看板" })).toHaveCount(0);
});

test("全部重试汇总数量", async ({ page, world }) => {
  world.failed = [failedTask(1, "a.mp4"), failedTask(2, "b.mp4")];
  await openApp(page);
  await column(page, "失败与重试看板").getByRole("button", { name: "全部重试" }).click();

  await expect(page.locator(".el-message--success")).toContainText("已重新排队 2 个");
  const queue = column(page, "排队就绪看板");
  await expect(queue).toContainText("a.mp4");
  await expect(queue).toContainText("b.mp4");
});

test("清除一条失败记录", async ({ page, world }) => {
  world.failed = [failedTask(3, "gone.mp4")];
  await openApp(page);
  await column(page, "失败与重试看板").getByRole("button", { name: "清除", exact: true }).click();
  await expect(page.locator(".el-message--success")).toContainText("已清除");
  await expect(page.getByText("gone.mp4")).toHaveCount(0);
});

test("确认后清除全部失败记录", async ({ page, world }) => {
  world.failed = [failedTask(1, "a.mp4"), failedTask(2, "b.mp4")];
  await openApp(page);
  await column(page, "失败与重试看板").getByRole("button", { name: "全部清除" }).click();
  const box = page.locator(".el-message-box");
  await expect(box).toContainText("本地文件不会动");
  await box.getByRole("button", { name: "清除" }).click();
  await expect(page.locator(".el-message--success")).toContainText("已清除 2 条");
  await expect(page.locator(".empty-board-wrap")).toBeVisible();
});

test("失败页签可以勾选后清除", async ({ page, world }) => {
  world.failed = [failedTask(9, "picked.mp4"), failedTask(10, "kept.mp4")];
  await openApp(page);
  await page.getByRole("button", { name: /失败重试/ }).click();
  await page.locator(".task-card").filter({ hasText: "picked.mp4" }).locator("input[type=checkbox]").check();
  await page.getByRole("button", { name: "清除选中 (1)" }).click();

  await expect(page.locator(".el-message--success")).toContainText("已清除 1 条");
  await expect(page.getByText("picked.mp4")).toHaveCount(0);
  await expect(page.getByText("kept.mp4")).toBeVisible();
});

test("失败列表翻到下一页", async ({ page, world }) => {
  world.failed = Array.from({ length: 51 }, (_, index) => failedTask(index + 1, `fail-${index + 1}.mp4`));
  await openApp(page);
  const failed = column(page, "失败与重试看板");
  await expect(failed).toContainText("fail-1.mp4");
  await expect(failed).toContainText("1 / 2 · 共 51");
  await failed.getByRole("button", { name: "下一页" }).click();
  await expect(failed).toContainText("fail-51.mp4");
  await expect(failed.getByText("fail-1.mp4")).toHaveCount(0);
});

test("过大文件可以切片，也可以交给个人号", async ({ page, world }) => {
  world.oversized = [
    task({
      id: 15,
      file_name: "huge.mkv",
      status: "oversized",
      file_size: 3_000_000_000,
      sliceable: true,
      slice_phase: "idle",
      slice_min_parts: 2,
    }),
  ];
  await openApp(page);
  const card = column(page, "过大待处理看板");

  await card.getByRole("button", { name: "切片后上传" }).click();
  const box = page.locator(".el-message-box");
  await expect(box).toContainText("至少 2 段");
  await box.locator("input").fill("3");
  await box.getByRole("button", { name: "开始切片" }).click();
  await expect(page.locator(".el-message--success")).toContainText("已加入切片");
  expect(world.slices).toEqual([{ id: 15, parts: 3 }]);

  await card.getByRole("button", { name: "用个人号上传" }).click();
  await expect(page.locator(".el-message--success").last()).toContainText("已交给个人号");
  expect(world.dispatches).toEqual([15]);
});

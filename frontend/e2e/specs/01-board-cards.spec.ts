import { column, openApp } from "../helpers/ui";
import { task } from "../helpers/world";
import { expect, test } from "../fixtures";

test("五列卡片按状态归位", async ({ page, world }) => {
  world.board = [
    task({
      id: 1,
      file_name: "up.mp4",
      status: "uploading",
      assigned_worker: "worker_1",
      percent: 40,
      current: 400,
      total: 1000,
      stage: "uploading",
    }),
    task({ id: 2, file_name: "wait.mkv", status: "pending" }),
    task({ id: 3, file_name: "cover.mp4", status: "preparing" }),
  ];
  world.oversized = [
    task({
      id: 4,
      file_name: "big.mkv",
      status: "oversized",
      file_size: 3_000_000_000,
      sliceable: true,
    }),
  ];
  world.failed = [
    task({
      id: 5,
      file_name: "bad.mp4",
      status: "failed",
      error: "上传被拒绝",
    }),
  ];

  await openApp(page);

  const uploading = column(page, "正在上传看板");
  await expect(uploading).toContainText("up.mp4");
  await expect(uploading.locator(".worker-chip")).toHaveText("worker_1");
  await expect(uploading.locator(".task-card")).toHaveClass(/uploading/);
  await expect(uploading.locator(".el-progress-bar__inner")).toHaveCSS("background-color", "rgb(40, 153, 90)");

  const oversized = column(page, "过大待处理看板");
  await expect(oversized).toContainText("big.mkv");
  await expect(oversized).toContainText("超过 2GB");
  await expect(oversized.locator(".task-card")).toHaveClass(/oversized/);

  const failed = column(page, "失败与重试看板");
  await expect(failed).toContainText("bad.mp4");
  await expect(failed).toContainText("上传被拒绝");
  await expect(failed.getByRole("button", { name: "全部重试" })).toBeVisible();
  await expect(failed.locator(".task-card")).toHaveClass(/failed/);

  const queue = column(page, "排队就绪看板");
  await expect(queue).toContainText("wait.mkv");
  await expect(queue).toContainText("cover.mp4");
  await expect(queue).toContainText("等待 Worker");
  await expect(queue).toContainText("生成封面中");
});

test("已分配的任务归入正在上传", async ({ page, world }) => {
  world.board = [
    task({
      id: 8,
      file_name: "assigned.mp4",
      status: "assigned",
      assigned_worker: "worker_1",
    }),
  ];

  await openApp(page);

  const uploading = column(page, "正在上传看板");
  await expect(uploading).toContainText("assigned.mp4");
  await expect(uploading).toContainText("已分配");
  await expect(uploading.locator(".worker-chip")).toHaveText("worker_1");
});

test("空看板与单个分类的空说明", async ({ page, world }) => {
  await openApp(page);
  await expect.poll(() => world.boardReads).toBeGreaterThan(0);

  await expect(page.locator(".empty-board-wrap .empty-title")).toHaveText("当前没有进行中的任务");
  await expect(page.locator(".empty-desc")).toHaveText("监听目录中若有新文件落盘，将自动加入调度序列");
  await expect(page.locator(".empty-state-card")).toHaveCount(0);

  await page.getByRole("button", { name: /过大待处理/ }).click();
  await expect(page.locator(".empty-board-wrap")).toBeHidden();
  await expect(page.locator(".empty-state-card")).toHaveText("当前分类下暂无任务");
});

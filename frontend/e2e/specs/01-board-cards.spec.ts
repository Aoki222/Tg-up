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

  const uploading = column(page, "Uploading");
  await expect(uploading).toContainText("up.mp4");
  await expect(uploading.locator(".worker-chip")).toHaveText("worker_1");
  await expect(uploading.locator(".task-card")).toHaveClass(/uploading/);
  await expect(uploading.locator(".el-progress-bar__inner")).toHaveCSS("background-color", "rgb(40, 153, 90)");

  const oversized = column(page, "Over 2GB");
  await expect(oversized).toContainText("big.mkv");
  await expect(oversized).toContainText("Over 2GB");
  await expect(oversized.locator(".task-card")).toHaveClass(/oversized/);

  const failed = column(page, "Failed");
  await expect(failed).toContainText("bad.mp4");
  await expect(failed).toContainText("上传被拒绝");
  await expect(failed.getByRole("button", { name: "Retry all" })).toBeVisible();
  await expect(failed.locator(".task-card")).toHaveClass(/failed/);

  const queue = column(page, "Queue");
  await expect(queue).toContainText("wait.mkv");
  await expect(queue).toContainText("cover.mp4");
  await expect(queue).toContainText("Waiting");
  await expect(queue).toContainText("Making cover");
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

  const uploading = column(page, "Uploading");
  await expect(uploading).toContainText("assigned.mp4");
  await expect(uploading).toContainText("Assigned");
  await expect(uploading.locator(".worker-chip")).toHaveText("worker_1");
});

test("空看板与单个分类的空说明", async ({ page, world }) => {
  await openApp(page);
  await expect.poll(() => world.boardReads).toBeGreaterThan(0);

  await expect(page.locator(".empty-board-wrap .empty-title")).toHaveText("Nothing in progress");
  await expect(page.locator(".empty-desc")).toHaveText("New files in a watched folder join the queue on their own.");
  await expect(page.locator(".empty-state-card")).toHaveCount(0);

  await page.getByRole("button", { name: /Over 2GB/ }).click();
  await expect(page.locator(".empty-board-wrap")).toBeHidden();
  await expect(page.locator(".empty-state-card")).toHaveText("Nothing in this view");
});

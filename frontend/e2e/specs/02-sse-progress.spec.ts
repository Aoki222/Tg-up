import { column, openApp } from "../helpers/ui";
import { task, type ProgressEvent } from "../helpers/world";
import { expect, test } from "../fixtures";

function liveEvent(overrides: Partial<ProgressEvent> = {}): ProgressEvent {
  return {
    task_id: 101,
    worker_name: "worker_1",
    file_name: "test_movie.mp4",
    current: 8000,
    total: 10000,
    percent: 80,
    stage: "uploading",
    speed_bps: 12_582_912,
    eta_seconds: 5,
    message: "",
    ...overrides,
  };
}

test("SSE 把进度、速率和剩余时间写进同一张卡片", async ({ page, world }) => {
  world.board = [
    task({
      id: 101,
      file_name: "test_movie.mp4",
      status: "uploading",
      assigned_worker: "worker_1",
      percent: 10,
      current: 1000,
      total: 10000,
      speed_bps: 0,
      eta_seconds: -1,
      stage: "uploading",
    }),
  ];

  await openApp(page);
  const card = page.locator(".task-card.uploading");
  await expect(card.locator(".speed-text")).toHaveText("测算中");
  await expect(card).toContainText("10%");
  await expect(page.locator(".telemetry-val")).toHaveText("测算中");

  await world.pushProgress(liveEvent());

  await expect(card).toContainText("80%");
  await expect(card.locator(".speed-text")).toHaveText("12.0 MB/s");
  await expect(card).toContainText("预计剩余:");
  await expect(card.locator(".eta-val")).toHaveText("00:05");
  await expect(page.locator(".telemetry-val")).toHaveText("12.0 MB/s");
  await expect(card.locator(".worker-chip")).toHaveText("worker_1");
});

test("后续轮询不会把 SSE 进度打回去", async ({ page, world }) => {
  world.board = [
    task({
      id: 101,
      file_name: "test_movie.mp4",
      status: "uploading",
      percent: 10,
      current: 1000,
      total: 10000,
      speed_bps: 0,
      stage: "uploading",
    }),
  ];
  await openApp(page);
  const card = page.locator(".task-card.uploading");
  await expect(card).toContainText("10%");
  const readsBefore = world.boardReads;

  await world.pushProgress(liveEvent());
  await expect(card.locator(".speed-text")).toHaveText("12.0 MB/s");

  await expect.poll(() => world.boardReads).toBeGreaterThan(readsBefore);
  await expect(card).toContainText("80%");
  await expect(card.locator(".speed-text")).toHaveText("12.0 MB/s");
  await expect(card.locator(".eta-val")).toHaveText("00:05");
});

test("更旧的字节不会把进度条打回去", async ({ page, world }) => {
  world.board = [
    task({
      id: 101,
      file_name: "test_movie.mp4",
      status: "uploading",
      percent: 10,
      current: 1000,
      total: 10000,
      stage: "uploading",
    }),
  ];
  await openApp(page);
  const card = page.locator(".task-card.uploading");
  await expect(card).toContainText("10%");
  await world.pushProgress(liveEvent());
  await expect(card).toContainText("80%");

  await world.pushProgress(liveEvent({ current: 1000, percent: 10, speed_bps: 1, eta_seconds: 90 }));
  await expect(card).toContainText("80%");
  await expect(card.locator(".speed-text")).toHaveText("12.0 MB/s");
});

test("换段后进度从新段重新计", async ({ page, world }) => {
  world.board = [
    task({
      id: 101,
      file_name: "test_movie.mp4",
      status: "uploading",
      percent: 80,
      current: 8000,
      total: 10000,
      speed_bps: 1000,
      stage: "uploading",
      part_index: 1,
      part_count: 3,
    }),
  ];
  await openApp(page);
  const card = page.locator(".task-card.uploading");
  await expect(card).toContainText("第 1/3 段");

  await world.pushProgress(
    liveEvent({
      current: 200,
      total: 5000,
      percent: 2,
      speed_bps: 1024,
      eta_seconds: 12,
      part_index: 2,
      part_count: 3,
    }),
  );

  await expect(card).toContainText("2%");
  await expect(card).toContainText("第 2/3 段");
  await expect(card.locator(".speed-text")).toHaveText("1.0 KB/s");
});

test("限流事件把卡片移到排队列", async ({ page, world }) => {
  world.board = [
    task({
      id: 101,
      file_name: "test_movie.mp4",
      status: "uploading",
      percent: 10,
      current: 1000,
      total: 10000,
      stage: "uploading",
    }),
  ];
  await openApp(page);
  await expect(column(page, "正在上传看板")).toContainText("test_movie.mp4");
  world.board = [
    task({
      id: 101,
      file_name: "test_movie.mp4",
      status: "pending",
    }),
  ];

  await world.pushProgress(liveEvent({ stage: "flood_wait", speed_bps: 0, eta_seconds: -1, message: "稍后再试" }));

  await expect(column(page, "排队就绪看板")).toContainText("test_movie.mp4");
  await expect(page.locator(".section-title", { hasText: "正在上传看板" })).toHaveCount(0);
});

test("成功后短暂留下卡片，随后从看板消失", async ({ page, world }) => {
  world.board = [
    task({
      id: 101,
      file_name: "test_movie.mp4",
      status: "uploading",
      percent: 90,
      current: 9000,
      total: 10000,
      stage: "uploading",
    }),
  ];
  await openApp(page);
  await expect(page.getByText("test_movie.mp4")).toBeVisible();
  world.board = [];

  await world.pushProgress(liveEvent({ stage: "success", percent: 100, current: 10000, speed_bps: 0, eta_seconds: -1, message: "上传成功" }));
  await expect(page.locator(".task-card")).toContainText("上传成功");
  await expect(page.getByText("test_movie.mp4")).toHaveCount(0);
});

test("失败事件把错误写进失败列", async ({ page, world }) => {
  world.board = [
    task({
      id: 101,
      file_name: "test_movie.mp4",
      status: "uploading",
      percent: 10,
      current: 1000,
      total: 10000,
      stage: "uploading",
    }),
  ];
  await openApp(page);
  await expect(page.getByText("test_movie.mp4")).toBeVisible();
  world.board = [];
  world.failed = [
    task({
      id: 101,
      file_name: "test_movie.mp4",
      status: "failed",
      error: "连接重置",
      message: "连接重置",
    }),
  ];

  await world.pushProgress(liveEvent({ stage: "failed", speed_bps: 0, eta_seconds: -1, message: "连接重置" }));
  const failed = column(page, "失败与重试看板");
  await expect(failed).toContainText("test_movie.mp4");
  await expect(failed).toContainText("连接重置");
});

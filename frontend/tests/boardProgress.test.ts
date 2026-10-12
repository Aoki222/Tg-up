import { expect, test } from "vitest";
import { mergeLiveBytes } from "../src/boardProgress";
import type { BoardTask, UploadProgress } from "../src/types";

function task(over: Partial<BoardTask> = {}): BoardTask {
  return {
    id: 1,
    file_name: "a.mp4",
    file_size: 1000,
    folder_name: null,
    status: "uploading",
    assigned_worker: "bot",
    platform: "telegram",
    dest_id: "-100",
    retry_count: 0,
    max_retries: 3,
    error: null,
    created_at: null,
    started_at: null,
    percent: 50,
    current: 500,
    total: 1000,
    speed_bps: 10,
    eta_seconds: 20,
    stage: "uploading",
    message: "",
    sliceable: false,
    slice_phase: "idle",
    slice_min_parts: null,
    part_index: 1,
    part_count: 3,
    ...over,
  };
}

function progress(over: Partial<UploadProgress> = {}): UploadProgress {
  return {
    task_id: 1,
    worker_name: "bot",
    file_name: "a.mp4",
    current: 100,
    total: 1000,
    percent: 10,
    stage: "uploading",
    message: "later",
    speed_bps: 5,
    eta_seconds: 40,
    part_index: 1,
    part_count: 3,
    ...over,
  };
}

test("older bytes do not overwrite the bar", () => {
  const next = mergeLiveBytes(task(), progress({ current: 100, percent: 10 }));
  expect(next.current).toBe(500);
  expect(next.percent).toBe(50);
  expect(next.total).toBe(1000);
});

test("a new part index restarts the byte count", () => {
  const next = mergeLiveBytes(
    task({ part_index: 1, current: 500, percent: 80 }),
    progress({ part_index: 2, part_count: 4, current: 12, total: 800, percent: 1 }),
  );
  expect(next.current).toBe(12);
  expect(next.percent).toBe(1);
  expect(next.total).toBe(800);
  expect(next.part_index).toBe(2);
  expect(next.part_count).toBe(4);
});

test("the first time a part index appears it does not zero the bar", () => {
  const next = mergeLiveBytes(
    task({ part_index: null, current: 500 }),
    progress({ part_index: 1, current: 0, percent: 0 }),
  );
  expect(next.current).toBe(500);
  expect(next.part_index).toBe(1);
});

/**
 * 看板数据：1 秒轮询 /api/tasks 决定列归属和计数。
 * 字节、百分比、速度只跟 SSE。轮询带回的更旧进度不覆盖界面。
 */
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref } from "vue";
import { fetchBoardTasks, openProgressStream } from "../api";
import { isAlbumProgress } from "../format";
import type { BoardCounts, BoardTask, UploadProgress } from "../types";

const EMPTY_COUNTS: BoardCounts = {
  preparing: 0,
  pending: 0,
  assigned: 0,
  uploading: 0,
  oversized: 0,
  failed: 0,
  success: 0,
  success_today: 0,
};

export function useTaskBoard() {
  const items = ref<BoardTask[]>([]);
  const counts = ref<BoardCounts>({ ...EMPTY_COUNTS });
  const ghosts = new Map<number, { task: BoardTask; hideAt: number }>();

  let pollTimer = 0;
  let ghostTimer = 0;
  let source: EventSource | null = null;
  let boardFlight = false;
  let live = false;

  const inFlightCount = computed(
    () =>
      items.value.filter(
        (item) =>
          (item.status === "assigned" || item.status === "uploading") && item.stage !== "success",
      ).length,
  );

  const queueCount = computed(() => counts.value.preparing + counts.value.pending);
  const successToday = computed(() => counts.value.success_today);
  const successTotal = computed(() => counts.value.success);

  function upsert(task: BoardTask): void {
    // 以任务 ID 合并数据库快照或 SSE 增量，避免替换掉另一来源的字段。
    const index = items.value.findIndex((item) => item.id === task.id);
    if (index >= 0) {
      const next = items.value.slice();
      next[index] = { ...items.value[index], ...task };
      items.value = next;
    } else {
      items.value = [...items.value, task];
    }
  }

  function progressToTask(progress: UploadProgress): BoardTask {
    // 将 SSE 的轻量进度事件补齐为看板可展示的任务模型。
    const failed = progress.stage === "failed";
    return {
      id: progress.task_id,
      file_name: progress.file_name,
      file_size: progress.total > 32 ? progress.total : 0,
      folder_name: null,
      status: failed ? "failed" : "uploading",
      assigned_worker: progress.worker_name,
      retry_count: 0,
      max_retries: 3,
      error: failed ? progress.message : null,
      created_at: null,
      started_at: null,
      percent: progress.percent,
      current: progress.current,
      total: progress.total,
      speed_bps: progress.speed_bps ?? 0,
      eta_seconds: progress.eta_seconds ?? -1,
      stage: progress.stage,
      message: progress.message,
      sliceable: false,
      slice_phase: "idle",
      slice_min_parts: null,
    };
  }

  function sliceLabel(text: string): string | null {
    const matched = text.match(/第 \d+\/\d+ 段/);
    return matched ? matched[0] : null;
  }

  function keepOversized(existing: BoardTask): boolean {
    return existing.status === "oversized";
  }

  function hasByteProgress(task: BoardTask): boolean {
    return task.total > 32 && !isAlbumProgress(task.current, task.total) && task.current > 0;
  }

  function keepByteProgress(existing: BoardTask, current: number, total: number): boolean {
    // 文件个数和更旧的字节数不能把已经显示的字节进度打回去。
    if (!hasByteProgress(existing)) return false;
    if (isAlbumProgress(current, total)) return true;
    return current < existing.current;
  }

  function mergePolled(incoming: BoardTask): BoardTask {
    // 轮询负责状态和列。同一次上传里，实时推流已经写过的进度留在界面上。
    const existing = items.value.find((item) => item.id === incoming.id);
    if (!existing || existing.stage == null) return incoming;
    const restarted =
      (existing.status === "pending" || existing.status === "failed") &&
      (incoming.status === "uploading" || incoming.status === "assigned");
    if (restarted) return incoming;
    return {
      ...incoming,
      percent: existing.percent,
      current: existing.current,
      total: existing.total,
      speed_bps: existing.speed_bps,
      eta_seconds: existing.eta_seconds,
      stage: existing.stage,
      message: existing.message,
    };
  }

  function applyProgress(progress: UploadProgress): void {
    // 合并实时进度；成功任务短暂保留为 ghost，等待下一次数据库快照确认。
    if (progress.stage === "success") {
      const existing = items.value.find((item) => item.id === progress.task_id);
      if (existing && keepOversized(existing)) return;
      const ghost: BoardTask = {
        ...(existing ?? progressToTask(progress)),
        status: "uploading",
        stage: "success",
        percent: 100,
        speed_bps: 0,
        eta_seconds: -1,
        message: progress.message || "上传成功",
      };
      ghosts.set(progress.task_id, { task: ghost, hideAt: Date.now() + 4000 });
      upsert(ghost);
      scheduleGhostSweep();
      return;
    }

    if (progress.stage === "failed") {
      ghosts.delete(progress.task_id);
      const existing = items.value.find((item) => item.id === progress.task_id);
      if (existing && keepOversized(existing)) {
        upsert({
          ...existing,
          stage: "failed",
          speed_bps: 0,
          eta_seconds: -1,
          message: progress.message,
          error: progress.message || existing.error,
        });
        return;
      }
      upsert({
        ...(existing ?? progressToTask(progress)),
        status: "failed",
        stage: "failed",
        speed_bps: 0,
        eta_seconds: -1,
        message: progress.message,
        error: progress.message || existing?.error || null,
      });
      return;
    }

    const existing = items.value.find((item) => item.id === progress.task_id);
    if (!existing) {
      if (progress.stage === "uploading") {
        upsert(progressToTask(progress));
      }
      return;
    }

    const nextStatus = keepOversized(existing)
      ? existing.status
      : progress.stage === "uploading"
        ? "uploading"
        : progress.stage === "flood_wait"
          ? "pending"
          : existing.status;

    // 仍在上传中时，更旧的字节或相册回调不能把条打回去。
    // 任务已经回到等待后再开始，新的 0 是另一次上传，要接受。
    // 切片换到下一段时，字节从这段的开头重新计。
    const previousPart = sliceLabel(existing.message);
    const nextPart = sliceLabel(progress.message);
    const partChanged = previousPart !== null && nextPart !== null && previousPart !== nextPart;
    const restart =
      progress.current <= 0 && existing.status !== "uploading" && existing.status !== "assigned";
    if (keepByteProgress(existing, progress.current, progress.total) && !restart && !partChanged) {
      upsert({
        ...existing,
        status: nextStatus,
        assigned_worker: progress.worker_name || existing.assigned_worker,
        message: progress.message || existing.message,
      });
      return;
    }

    upsert({
      ...existing,
      status: nextStatus,
      percent: progress.percent,
      current: progress.current,
      total: progress.total,
      speed_bps: progress.speed_bps ?? 0,
      eta_seconds: progress.eta_seconds ?? -1,
      stage: progress.stage,
      message: progress.message,
      assigned_worker: progress.worker_name || existing.assigned_worker,
    });
  }

  async function refresh(): Promise<void> {
    // 上一轮还没回来就跳过本次，避免 1 秒定时器叠出并发请求。
    if (boardFlight) return;
    boardFlight = true;
    try {
      const data = await fetchBoardTasks();
      const byId = new Map(data.items.map((item) => [item.id, mergePolled(item)]));
      const now = Date.now();
      for (const [id, ghost] of [...ghosts.entries()]) {
        if (now >= ghost.hideAt || byId.has(id)) {
          ghosts.delete(id);
        } else {
          byId.set(id, ghost.task);
        }
      }
      items.value = [...byId.values()];
      counts.value = { ...EMPTY_COUNTS, ...data.counts };
    } catch {
      // 保留上一帧，避免轮询闪断清空看板
    } finally {
      boardFlight = false;
    }
  }

  function scheduleGhostSweep(): void {
    // 删除已展示完成态的 ghost，避免成功任务永久停留在上传列。
    window.clearTimeout(ghostTimer);
    let soonest = Infinity;
    const now = Date.now();
    for (const [id, ghost] of [...ghosts.entries()]) {
      if (now >= ghost.hideAt) {
        ghosts.delete(id);
        items.value = items.value.filter((item) => item.id !== id);
      } else {
        soonest = Math.min(soonest, ghost.hideAt);
      }
    }
    if (soonest !== Infinity) {
      ghostTimer = window.setTimeout(scheduleGhostSweep, Math.max(16, soonest - Date.now()));
    }
  }

  function start(): void {
    if (live) return;
    live = true;
    void refresh();
    source = openProgressStream(applyProgress);
    pollTimer = window.setInterval(() => {
      void refresh();
    }, 1000);
    if (ghosts.size > 0) scheduleGhostSweep();
  }

  function stop(): void {
    if (!live) return;
    live = false;
    source?.close();
    source = null;
    window.clearInterval(pollTimer);
    pollTimer = 0;
    window.clearTimeout(ghostTimer);
    ghostTimer = 0;
  }

  onMounted(start);
  onActivated(start);
  onDeactivated(stop);
  onUnmounted(stop);

  return { items, counts, inFlightCount, queueCount, successToday, successTotal };
}

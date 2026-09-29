/**
 * 看板数据：1 秒轮询 /api/tasks 决定列归属，SSE 叠字节与速度。
 */
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref } from "vue";
import { fetchBoardTasks, openProgressStream } from "../api";
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
    };
  }

  function applyProgress(progress: UploadProgress): void {
    // 合并实时进度；成功任务短暂保留为 ghost，等待下一次数据库快照确认。
    if (progress.stage === "success") {
      const existing = items.value.find((item) => item.id === progress.task_id);
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

    const nextStatus =
      progress.stage === "uploading"
        ? "uploading"
        : progress.stage === "flood_wait"
          ? "pending"
          : existing.status;

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
      const byId = new Map(data.items.map((item) => [item.id, item]));
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

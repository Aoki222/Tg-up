/**
 * 看板数据：1 秒轮询 /api/tasks 决定列归属，SSE 叠字节与速度。
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import { fetchBoardTasks, fetchProgressSnapshot, openProgressStream } from "../api";
import { isAlbumProgress } from "../format";
import type { BoardCounts, BoardTask, UploadProgress } from "../types";

const EMPTY_COUNTS: BoardCounts = {
  preparing: 0,
  pending: 0,
  assigned: 0,
  uploading: 0,
  failed: 0,
};

export function useTaskBoard() {
  const items = ref<BoardTask[]>([]);
  const counts = ref<BoardCounts>({ ...EMPTY_COUNTS });
  const ghosts = new Map<number, { task: BoardTask; hideAt: number }>();

  let pollTimer = 0;
  let ghostTimer = 0;
  let source: EventSource | null = null;

  const totalSpeed = computed(() => {
    let speed = 0;
    for (const item of items.value) {
      if (item.status !== "uploading" || item.stage === "success") continue;
      if (isAlbumProgress(item.current, item.total)) continue;
      speed += item.speed_bps || 0;
    }
    return speed;
  });

  const inFlightCount = computed(
    () =>
      items.value.filter(
        (item) =>
          (item.status === "assigned" || item.status === "uploading") && item.stage !== "success",
      ).length,
  );

  const queueCount = computed(() => counts.value.preparing + counts.value.pending);

  function upsert(task: BoardTask): void {
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
      counts.value = data.counts;
    } catch {
      // 保留上一帧，避免轮询闪断清空看板
    }
  }

  function scheduleGhostSweep(): void {
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

  onMounted(() => {
    void refresh();
    void fetchProgressSnapshot()
      .then((list) => {
        for (const item of list) applyProgress(item);
      })
      .catch(() => undefined);
    source = openProgressStream(applyProgress);
    pollTimer = window.setInterval(() => {
      void refresh();
    }, 1000);
  });

  onUnmounted(() => {
    source?.close();
    window.clearInterval(pollTimer);
    window.clearTimeout(ghostTimer);
  });

  return { items, counts, totalSpeed, inFlightCount, queueCount };
}

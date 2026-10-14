/**
 * 看板数据：约 4 秒轮询进行中的任务，失败和过大另按页拉取。
 * 字节、百分比、速度只跟 SSE。轮询带回的更旧进度不覆盖界面。
 * 切片换段看 part_index，不解析中文说明。
 */
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref } from "vue";
import { authRequired, fetchBoardTasks, fetchStatusPage, openProgressStream } from "../api";
import { mergeLiveBytes } from "../boardProgress";
import type { BoardCounts, BoardTask, UploadProgress } from "../types";
import { t } from "../i18n";

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
  const failedPage = ref(1);
  const oversizedPage = ref(1);
  const failedTotal = ref(0);
  const oversizedTotal = ref(0);
  const pageSize = 50;
  const ghosts = new Map<number, { task: BoardTask; hideAt: number }>();

  let pollTimer = 0;
  let ghostTimer = 0;
  let source: EventSource | null = null;
  let boardFlight = false;
  let refreshAgain = false;
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
      platform: "telegram",
      dest_id: "",
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
      part_index: progress.part_index ?? null,
      part_count: progress.part_count ?? null,
    };
  }

  function keepOversized(existing: BoardTask): boolean {
    return existing.status === "oversized";
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
        message: progress.message || t("card.uploaded"),
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

    upsert(mergeLiveBytes(existing, progress));
  }

  async function refresh(): Promise<void> {
    if (authRequired.value) return;
    // 上一轮还没回来就记一笔，结束后用最新页码再拉一次。
    if (boardFlight) {
      refreshAgain = true;
      return;
    }
    boardFlight = true;
    const failedAt = failedPage.value;
    const oversizedAt = oversizedPage.value;
    try {
      const [data, failed, oversized] = await Promise.all([
        fetchBoardTasks(),
        fetchStatusPage("failed", failedAt, pageSize),
        fetchStatusPage("oversized", oversizedAt, pageSize),
      ]);
      const failedPages = Math.max(1, Math.ceil(failed.total / pageSize));
      const oversizedPages = Math.max(1, Math.ceil(oversized.total / pageSize));
      if (failedPage.value > failedPages) failedPage.value = failedPages;
      if (oversizedPage.value > oversizedPages) oversizedPage.value = oversizedPages;
      if (failedPage.value !== failedAt || oversizedPage.value !== oversizedAt) return;
      failedTotal.value = failed.total;
      oversizedTotal.value = oversized.total;
      const combined = [...data.items, ...failed.items, ...oversized.items];
      const byId = new Map(combined.map((item) => [item.id, mergePolled(item)]));
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
      if (refreshAgain || failedPage.value !== failedAt || oversizedPage.value !== oversizedAt) {
        refreshAgain = false;
        void refresh();
      }
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
    void openProgressStream(applyProgress).then((opened) => {
      if (!live) {
        opened.close();
        return;
      }
      source = opened;
    });
    pollTimer = window.setInterval(() => {
      void refresh();
    }, 4000);
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

  function setFailedPage(page: number): void {
    failedPage.value = Math.max(1, page);
    void refresh();
  }

  function setOversizedPage(page: number): void {
    oversizedPage.value = Math.max(1, page);
    void refresh();
  }

  return {
    items,
    counts,
    inFlightCount,
    queueCount,
    successToday,
    successTotal,
    failedPage,
    oversizedPage,
    failedTotal,
    oversizedTotal,
    pageSize,
    setFailedPage,
    setOversizedPage,
  };
}

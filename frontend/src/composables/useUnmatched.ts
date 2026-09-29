/**
 * 未命中文件：监控和设置共用一份结果。只有当前可见的页面每 5 秒拉一次。
 */
import { onActivated, onDeactivated, onMounted, onUnmounted, ref } from "vue";
import { fetchUnmatched } from "../api";
import type { UnmatchedFile } from "../types";

const files = ref<UnmatchedFile[]>([]);
let timer = 0;
let inflight = false;
let ownerId = 0;
let nextId = 0;

async function refresh(): Promise<void> {
  if (inflight) return;
  inflight = true;
  try {
    files.value = await fetchUnmatched();
  } catch {
    // 保留上一帧，切回来时不把横幅闪成空。
  } finally {
    inflight = false;
  }
}

function stopTimer(): void {
  window.clearInterval(timer);
  timer = 0;
}

export function useUnmatchedFiles() {
  return { files };
}

export function useUnmatchedPolling() {
  const id = ++nextId;
  let running = false;

  function start(): void {
    if (running) return;
    running = true;
    stopTimer();
    ownerId = id;
    void refresh();
    timer = window.setInterval(() => {
      void refresh();
    }, 5000);
  }

  function stop(): void {
    if (!running) return;
    running = false;
    if (ownerId !== id) return;
    ownerId = 0;
    stopTimer();
  }

  onMounted(start);
  onActivated(start);
  onDeactivated(stop);
  onUnmounted(stop);

  return { files };
}

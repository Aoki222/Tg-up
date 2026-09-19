<script setup lang="ts">
/**
 * 看板单卡。四种主体：封面 / 等待 / 已分配或上传中 / 失败。
 */
import type { BoardTask } from "../types";
import {
  formatBytes,
  formatETA,
  formatSpeed,
  formatTransferred,
  isAlbumProgress,
} from "../format";

const { task, retrying } = defineProps<{ task: BoardTask; retrying?: boolean }>();

const emit = defineEmits<{
  retry: [id: number];
}>();

function pendingHint(item: BoardTask): string | null {
  const text = item.message || item.error || "";
  if (!text) return null;
  const lower = text.toLowerCase();
  if (lower.includes("manual retry")) return "手动重试";
  if (
    lower.includes("flood") ||
    lower.includes("disconnect") ||
    lower.includes("timeout") ||
    text.includes("限流")
  ) {
    return text.length > 80 ? `${text.slice(0, 80)}…` : text;
  }
  return null;
}

function speedLabel(item: BoardTask): string {
  if (item.speed_bps > 0) return formatSpeed(item.speed_bps);
  if (item.percent <= 0) return "测算中";
  return "0 B/s";
}
</script>

<template>
  <article class="card" :class="task.status">
    <div class="name" :title="task.file_name">{{ task.file_name }}</div>
    <div class="meta">
      <span v-if="task.file_size > 0">{{ formatBytes(task.file_size) }}</span>
      <span v-if="task.folder_name">{{ task.folder_name }}</span>
    </div>

    <div v-if="task.status === 'preparing'" class="body">
      <p class="hint">正在生成封面</p>
      <div class="rail" aria-hidden="true">
        <span class="rail-fill shimmer"></span>
      </div>
    </div>

    <div v-else-if="task.status === 'pending'" class="body">
      <p class="hint">等待 Worker</p>
      <p v-if="pendingHint(task)" class="warn">{{ pendingHint(task) }}</p>
    </div>

    <div v-else-if="task.status === 'assigned'" class="body">
      <p class="hint">已分配{{ task.assigned_worker ? ` · ${task.assigned_worker}` : "" }}</p>
    </div>

    <div v-else-if="task.status === 'uploading'" class="body">
      <el-progress
        :percentage="task.percent"
        :status="task.stage === 'success' ? 'success' : task.stage === 'failed' ? 'exception' : undefined"
        :stroke-width="5"
        :show-text="false"
      />
      <div class="stats">
        <span>{{ formatTransferred(task.current, task.total) }} · {{ task.percent }}%</span>
        <span v-if="task.stage === 'success'" class="ok">上传成功</span>
        <span v-else-if="!isAlbumProgress(task.current, task.total)">
          {{ speedLabel(task) }} · 剩余 {{ formatETA(task.eta_seconds) }}
        </span>
      </div>
      <p v-if="task.assigned_worker && task.stage !== 'success'" class="worker">{{ task.assigned_worker }}</p>
    </div>

    <div v-else class="body">
      <p v-if="task.error || task.message" class="fail">{{ task.error || task.message }}</p>
      <div class="fail-row">
        <p class="retry">重试 {{ task.retry_count }}/{{ task.max_retries }}</p>
        <button
          type="button"
          class="retry-btn"
          :disabled="retrying"
          @click="emit('retry', task.id)"
        >
          {{ retrying ? "重试中" : "重试" }}
        </button>
      </div>
    </div>
  </article>
</template>

<style scoped>
.card {
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
  transition: border-color 0.2s cubic-bezier(0.32, 0.72, 0, 1);
}

.card:hover {
  border-color: rgba(0, 0, 0, 0.12);
}

.name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text);
}

.meta {
  display: flex;
  gap: 8px;
  margin-top: 4px;
  font-size: 11.5px;
  color: var(--text-secondary);
}

.body {
  margin-top: 10px;
}

.hint {
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.warn {
  margin: 6px 0 0;
  padding: 5px 8px;
  border-radius: 6px;
  background: #fdf5ea;
  color: var(--warn);
  font-size: 11.5px;
  word-break: break-all;
}

.fail {
  margin: 0;
  padding: 5px 8px;
  border-radius: 6px;
  background: #fdf5f5;
  color: var(--bad);
  font-size: 11.5px;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-all;
}

.fail-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 6px;
}

.retry {
  margin: 0;
  font-size: 11px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

.retry-btn {
  flex-shrink: 0;
  padding: 3px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition:
    background-color 0.15s cubic-bezier(0.32, 0.72, 0, 1),
    border-color 0.15s cubic-bezier(0.32, 0.72, 0, 1),
    transform 0.15s cubic-bezier(0.32, 0.72, 0, 1);
}

.retry-btn:hover:not(:disabled) {
  border-color: rgba(0, 0, 0, 0.12);
  background: var(--hover);
}

.retry-btn:active:not(:disabled) {
  transform: scale(0.97);
}

.retry-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.stats {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
  font-family: ui-monospace, "SF Mono", "JetBrains Mono", monospace;
}

.ok {
  color: var(--ok);
  font-weight: 500;
}

.worker {
  margin: 6px 0 0;
  font-size: 11px;
  color: var(--text-secondary);
}

.rail {
  position: relative;
  height: 5px;
  margin-top: 8px;
  overflow: hidden;
  border-radius: 9999px;
  background: #e8ede9;
}

.rail-fill {
  position: absolute;
  top: 0;
  left: 0;
  width: 40%;
  height: 100%;
  border-radius: 9999px;
  background: var(--accent);
}

.shimmer {
  animation: rail-shimmer 1.2s cubic-bezier(0.32, 0.72, 0, 1) infinite;
}

@keyframes rail-shimmer {
  0% {
    transform: translateX(-60%);
  }
  100% {
    transform: translateX(220%);
  }
}

@media (prefers-reduced-motion: reduce) {
  .shimmer {
    animation: none;
    transform: none;
  }
}
</style>

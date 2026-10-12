<script setup lang="ts">
/**
 * @file TaskCard.vue
 * @description 看板任务卡片 · 100% 对齐 prototype/app_interactive_prototype.html 调色板与规范
 *
 * 状态配色规范：
 * 1. 上传中 (uploading)：翠绿 #28995a 进度条与速率文本、传输中胶囊、Worker 专属标识
 * 2. 过大待处理 (oversized)：暖琥珀 bg-amber-50/70 border-amber-200/80 卡片、超过 2GB 胶囊、切片后上传金黄按钮
 * 3. 失败重试 (failed)：柔红 bg-red-50/70 border-red-200/80 卡片、传输中断胶囊、失败尺寸红标、重试红钮与错误日志折叠面板
 * 4. 排队就绪 (pending/preparing)：紧凑横向行、状态圆点、等待 Worker 胶囊、轻量清除按钮
 */

import { ref } from "vue";
import type { BoardTask } from "../types";
import {
  formatBytes,
  formatETA,
  formatSpeed,
  formatTransferred,
  isAlbumProgress,
} from "../format";

const { task, retrying, selected, selectable, dispatching } = defineProps<{
  task: BoardTask;
  retrying?: boolean;
  selected?: boolean;
  selectable?: boolean;
  dispatching?: boolean;
}>();

const emit = defineEmits<{
  retry: [id: number];
  remove: [id: number];
  toggle: [id: number];
  dispatch: [id: number];
  slice: [id: number];
  continue: [id: number];
}>();

const showErrorLog = ref(false);

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

function oversizedHint(item: BoardTask): string {
  const phase = item.slice_phase || "idle";
  if (phase === "queued") return "等待切片";
  if (phase === "cutting" && item.stage !== "uploading") return "正在切片";
  if (phase === "released") return item.error || "分段已进入队列";
  if (phase === "uploading" || item.stage === "uploading") {
    return item.message || item.error || "正在上传切片";
  }
  if (phase === "failed") return item.error || "切片上传失败";
  if (phase === "blocked") return item.error || "切片未完成";
  return "单文件超过 Telegram 普通通道 2.0GB 限制，等待个人号或切片后上传。";
}

function showSlice(item: BoardTask): boolean {
  const phase = item.slice_phase || "idle";
  return Boolean(item.sliceable) && (phase === "idle" || phase === "blocked");
}

function showDispatch(item: BoardTask): boolean {
  const phase = item.slice_phase || "idle";
  return phase === "idle" || phase === "blocked";
}

function showContinue(item: BoardTask): boolean {
  return item.slice_phase === "failed";
}

function partLabel(item: BoardTask): string {
  if (item.status === "oversized" || !item.part_index || !item.part_count) return "";
  return `第 ${item.part_index}/${item.part_count} 段`;
}

function speedLabel(item: BoardTask): string {
  if (item.speed_bps > 0) return formatSpeed(item.speed_bps);
  return "测算中";
}
</script>

<template>
  <article
    class="task-card"
    :class="[
      task.status,
      {
        selected: selected,
        compact: task.status === 'pending' || task.status === 'preparing' || task.status === 'assigned',
      },
    ]"
  >
    <!-- ══════════════════════════════════════════════════════════
         1. 正在上传卡片 (Uploading)
    ══════════════════════════════════════════════════════════ -->
    <template v-if="task.status === 'uploading'">
      <div class="card-head">
        <div class="head-left">
          <label v-if="selectable" class="pick" @click.stop>
            <input type="checkbox" :checked="selected" @change="emit('toggle', task.id)" />
          </label>
          <span v-if="task.assigned_worker" class="worker-chip">
            {{ task.assigned_worker }}
          </span>
          <div class="card-title truncate" :title="task.file_name">
            {{ task.file_name }}
          </div>
          <span v-if="partLabel(task)" class="part-chip">{{ partLabel(task) }}</span>
        </div>

        <div class="head-right shrink-0">
          <span class="speed-text font-mono font-bold">{{ speedLabel(task) }}</span>
          <span class="badge-chip uploading">传输中</span>
        </div>
      </div>

      <!-- 源与目标路径条 -->
      <div class="route-line truncate">
        <span>源: <code>{{ task.folder_name || '默认监听目录' }}</code></span>
        <span class="route-arrow">➔</span>
        <span>目标: <span class="dest-name font-medium">Telegram</span></span>
      </div>

      <!-- 进度条 (翠绿 #28995a) -->
      <div class="progress-wrap">
        <el-progress
          :percentage="task.percent"
          :color="'#28995a'"
          :status="task.stage === 'success' ? 'success' : undefined"
          :stroke-width="7"
          :show-text="false"
        />
      </div>

      <!-- 进度详情数据条 -->
      <div class="progress-stats font-mono">
        <span>{{ task.percent }}% ({{ formatTransferred(task.current, task.total) }})</span>
        <div v-if="task.stage === 'success'" class="stat-ok font-sans font-semibold">上传成功</div>
        <div v-else-if="!isAlbumProgress(task.current, task.total)">
          预计剩余: <span class="eta-val font-sans font-semibold">{{ formatETA(task.eta_seconds) }}</span>
        </div>
      </div>
    </template>

    <!-- ══════════════════════════════════════════════════════════
         2. 过大待处理卡片 (Oversized, 超过 2GB)
    ══════════════════════════════════════════════════════════ -->
    <template v-else-if="task.status === 'oversized'">
      <div class="card-head">
        <div class="head-left">
          <label v-if="selectable" class="pick" @click.stop>
            <input type="checkbox" :checked="selected" @change="emit('toggle', task.id)" />
          </label>
          <span class="badge-chip oversized">超过 2GB</span>
          <div class="card-title truncate" :title="task.file_name">
            {{ task.file_name }}
          </div>
        </div>

        <div class="head-right shrink-0">
          <span class="card-size-oversized font-mono font-bold">{{ formatBytes(task.file_size) }}</span>
        </div>
      </div>

      <div class="route-line oversized-text space-y-1">
        <div>
          <span>来源: <code class="code-oversized">{{ task.folder_name || '默认监听目录' }}</code></span>
          <span class="route-arrow">➔</span>
          <span>目标: <span class="font-semibold">Telegram</span></span>
        </div>
        <div class="hint-oversized">{{ oversizedHint(task) }}</div>
      </div>

      <!-- 若切片正在上传，展示进度条 -->
      <template v-if="task.slice_phase === 'uploading' || task.stage === 'uploading'">
        <div class="progress-wrap">
          <el-progress
            :percentage="task.percent"
            :color="'#f59e0b'"
            :stroke-width="6"
            :show-text="false"
          />
        </div>
        <div class="progress-stats font-mono text-amber-900">
          <span>{{ task.percent }}% ({{ formatTransferred(task.current, task.total) }})</span>
          <span>剩余 {{ formatETA(task.eta_seconds) }}</span>
        </div>
      </template>

      <!-- 卡片专属真实操作按钮条 -->
      <div class="action-footer oversized-footer">
        <div class="footer-left">
          <button
            v-if="showSlice(task)"
            type="button"
            class="btn-amber-solid"
            :disabled="retrying"
            @click="emit('slice', task.id)"
          >
            切片后上传
          </button>
          <button
            v-if="showContinue(task)"
            type="button"
            class="btn-amber-solid"
            :disabled="retrying"
            @click="emit('continue', task.id)"
          >
            继续
          </button>
          <button
            v-if="showDispatch(task)"
            type="button"
            class="btn-amber-outline"
            :disabled="retrying"
            @click="emit('dispatch', task.id)"
          >
            {{ dispatching ? "提交中" : "用个人号上传" }}
          </button>
        </div>

        <div class="footer-right">
          <button
            type="button"
            class="btn-red-outline"
            :disabled="retrying"
            @click="emit('remove', task.id)"
          >
            清除
          </button>
        </div>
      </div>
    </template>

    <!-- ══════════════════════════════════════════════════════════
         3. 失败与重试卡片 (Failed)
    ══════════════════════════════════════════════════════════ -->
    <template v-else-if="task.status === 'failed'">
      <div class="card-head">
        <div class="head-left">
          <label v-if="selectable" class="pick" @click.stop>
            <input type="checkbox" :checked="selected" @change="emit('toggle', task.id)" />
          </label>
          <span class="badge-chip failed">传输中断</span>
          <div class="card-title truncate" :title="task.file_name">
            {{ task.file_name }}
          </div>
        </div>

        <div class="head-right shrink-0">
          <span class="card-size-failed font-mono font-bold">
            失败 · {{ formatBytes(task.file_size) }}
          </span>
        </div>
      </div>

      <div class="route-line failed-text space-y-1">
        <div>
          <span>来源: <code class="code-failed">{{ task.folder_name || '默认监听目录' }}</code></span>
          <span class="route-arrow">➔</span>
          <span>目标: <span class="font-semibold">Telegram</span></span>
        </div>
        <div v-if="task.error || task.message" class="error-msg-box font-mono">
          {{ task.error || task.message }}
        </div>
      </div>

      <!-- 可展开的完整日志视图 -->
      <div v-if="showErrorLog && (task.error || task.message)" class="full-log-box font-mono">
        {{ task.error || task.message }}
      </div>

      <!-- 卡片专属真实操作按钮条 -->
      <div class="action-footer failed-footer">
        <div class="footer-left">
          <span class="retry-counter font-mono">重试 {{ task.retry_count }}/{{ task.max_retries }}</span>
          <button
            type="button"
            class="btn-red-solid"
            :disabled="retrying"
            @click="emit('retry', task.id)"
          >
            {{ retrying ? "重试中" : "重试" }}
          </button>
          <button
            v-if="task.error || task.message"
            type="button"
            class="btn-gray-outline"
            @click="showErrorLog = !showErrorLog"
          >
            {{ showErrorLog ? "收起日志" : "查看错误日志" }}
          </button>
        </div>

        <div class="footer-right">
          <button
            type="button"
            class="btn-red-outline"
            :disabled="retrying"
            @click="emit('remove', task.id)"
          >
            清除
          </button>
        </div>
      </div>
    </template>

    <!-- ══════════════════════════════════════════════════════════
         4. 排队就绪卡片 (Pending / Preparing)
    ══════════════════════════════════════════════════════════ -->
    <template v-else-if="task.status === 'pending' || task.status === 'preparing'">
      <div class="compact-row">
        <div class="compact-left">
          <label v-if="selectable" class="pick" @click.stop>
            <input type="checkbox" :checked="selected" @change="emit('toggle', task.id)" />
          </label>
          <span
            class="compact-dot"
            :class="pendingHint(task) ? 'dot-amber' : 'dot-gray'"
          ></span>
          <div class="compact-info truncate">
            <div class="compact-title truncate" :title="task.file_name">
              {{ task.file_name }}
            </div>
            <div class="compact-sub font-mono">
              {{ formatBytes(task.file_size) }} · 来源: {{ task.folder_name || '默认监听目录' }}
            </div>
          </div>
        </div>

        <div class="compact-right shrink-0">
          <span
            class="badge-chip font-mono"
            :class="pendingHint(task) ? 'queue-warn' : 'queue'"
          >
            {{ task.status === 'preparing' ? '生成封面中' : '等待 Worker' }}
          </span>
          <button
            type="button"
            class="btn-gray-outline btn-compact-clear"
            :disabled="retrying"
            @click="emit('remove', task.id)"
          >
            清除
          </button>
        </div>
      </div>

      <!-- 封面生成中动画条 -->
      <div v-if="task.status === 'preparing'" class="preparing-rail" aria-hidden="true">
        <span class="rail-fill shimmer"></span>
      </div>

      <!-- 等待原因提示 -->
      <div v-if="pendingHint(task)" class="pending-warn-box font-mono">
        {{ pendingHint(task) }}
      </div>
    </template>

    <!-- ══════════════════════════════════════════════════════════
         5. 已分配卡片 (Assigned)
    ══════════════════════════════════════════════════════════ -->
    <template v-else-if="task.status === 'assigned'">
      <div class="compact-row">
        <div class="compact-left">
          <label v-if="selectable" class="pick" @click.stop>
            <input type="checkbox" :checked="selected" @change="emit('toggle', task.id)" />
          </label>
          <span v-if="task.assigned_worker" class="worker-chip">
            {{ task.assigned_worker }}
          </span>
          <div class="compact-info truncate">
            <div class="compact-title truncate" :title="task.file_name">
              {{ task.file_name }}
            </div>
            <div class="compact-sub font-mono">
              {{ formatBytes(task.file_size) }} · 已分配就绪
            </div>
          </div>
        </div>

        <div class="compact-right shrink-0">
          <span class="badge-chip uploading font-mono">已分配</span>
          <button
            type="button"
            class="btn-gray-outline btn-compact-clear"
            :disabled="retrying"
            @click="emit('remove', task.id)"
          >
            清除
          </button>
        </div>
      </div>
    </template>
  </article>
</template>

<style scoped>
/* ── 卡片通用容器 ────────────────────────────────────────── */
.task-card {
  padding: 14px 16px;
  border-radius: var(--radius-md, 10px);
  border: 1px solid rgba(0, 0, 0, 0.06);
  background: rgba(249, 250, 251, 0.85);
  display: flex;
  flex-direction: column;
  gap: 10px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.02);
  transition: all 0.15s ease;
}

.task-card:hover {
  border-color: rgba(0, 0, 0, 0.12);
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.04);
}

.task-card.compact {
  padding: 10px 14px;
  gap: 6px;
}

.task-card.selected {
  border-color: rgba(40, 153, 90, 0.4);
  background: rgba(40, 153, 90, 0.04);
}

/* ── 状态差异化主题 ──────────────────────────────────────── */

/* 1. 正在上传 */
.task-card.uploading {
  background: #ffffff;
  border-color: rgba(0, 0, 0, 0.07);
}

/* 2. 过大待处理 (暖琥珀主题) */
.task-card.oversized {
  background: rgba(254, 243, 199, 0.45);
  border-color: rgba(253, 230, 138, 0.85);
}

.task-card.oversized:hover {
  border-color: rgba(245, 158, 11, 0.6);
}

/* 3. 失败重试 (柔红主题) */
.task-card.failed {
  background: rgba(254, 242, 242, 0.55);
  border-color: rgba(254, 202, 202, 0.85);
}

.task-card.failed:hover {
  border-color: rgba(239, 68, 68, 0.6);
}

/* ── 卡片头部区 ─────────────────────────────────────────── */
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.head-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}

.head-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.card-title {
  font-size: 14px;
  font-weight: 700;
  color: #111827;
  line-height: 1.4;
}

/* ── 各种胶囊标签 (Badges & Chips) ───────────────────────── */
.worker-chip {
  padding: 2px 7px;
  border-radius: var(--radius-sm, 6px);
  background: #e5e7eb;
  color: #374151;
  font-size: 11.5px;
  font-weight: 600;
  font-family: ui-monospace, SFMono-Regular, monospace;
  flex-shrink: 0;
}

.part-chip {
  padding: 2px 7px;
  border-radius: var(--radius-full, 9999px);
  background: rgba(40, 153, 90, 0.1);
  color: #28995a;
  font-size: 11.5px;
  font-weight: 600;
  flex-shrink: 0;
}

.badge-chip {
  padding: 2.5px 8px;
  border-radius: var(--radius-sm, 6px);
  font-size: 11.5px;
  font-weight: 700;
  line-height: 1.3;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
}

.badge-chip.uploading {
  background: rgba(40, 153, 90, 0.1);
  color: #28995a;
  border: 1px solid rgba(40, 153, 90, 0.2);
}

.badge-chip.oversized {
  background: #fde68a;
  color: #78350f;
}

.badge-chip.failed {
  background: #fecaca;
  color: #7f1d1d;
}

.badge-chip.queue {
  background: #e5e7eb;
  color: #4b5563;
}

.badge-chip.queue-warn {
  background: #fef3c7;
  color: #92400e;
}

/* ── 顶部右侧文字 ─────────────────────────────────────────── */
.speed-text {
  font-size: 13px;
  color: #28995a;
}

.card-size-oversized {
  font-size: 13px;
  color: #78350f;
}

.card-size-failed {
  font-size: 13px;
  color: #991b1b;
}

/* ── 路径与提示行 ─────────────────────────────────────────── */
.route-line {
  font-size: 12.5px;
  color: #6b7280;
  line-height: 1.4;
}

.route-line code {
  background: rgba(0, 0, 0, 0.04);
  padding: 1.5px 6px;
  border-radius: var(--radius-sm, 6px);
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 12px;
  color: #374151;
}

.route-arrow {
  margin: 0 4px;
  color: #9ca3af;
}

.dest-name {
  color: #1f2937;
}

.oversized-text {
  color: #92400e;
}

.code-oversized {
  background: rgba(254, 243, 199, 0.8) !important;
  color: #78350f !important;
}

.hint-oversized {
  font-size: 12px;
  color: #b45309;
}

.failed-text {
  color: #991b1b;
}

.code-failed {
  background: rgba(254, 226, 226, 0.8) !important;
  color: #7f1d1d !important;
}

.error-msg-box {
  margin-top: 4px;
  padding: 7px 10px;
  border-radius: var(--radius-sm, 6px);
  background: rgba(254, 226, 226, 0.6);
  color: #b91c1c;
  font-size: 12px;
  line-height: 1.45;
  word-break: break-all;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.full-log-box {
  background: #111827;
  color: #4ade80;
  font-size: 12px;
  line-height: 1.55;
  padding: 10px;
  border-radius: var(--radius-md, 10px);
  max-height: 160px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

/* ── 进度条与数据统计 ─────────────────────────────────────── */
.progress-wrap {
  width: 100%;
}

.progress-wrap :deep(.el-progress-bar__outer) {
  background: #e5e7eb !important;
  border-radius: var(--radius-full, 9999px);
}

.progress-wrap :deep(.el-progress-bar__inner) {
  border-radius: var(--radius-full, 9999px);
  transition: width 0.3s ease;
}

.progress-stats {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12.5px;
  color: #6b7280;
}

.stat-ok {
  color: #28995a;
}

.eta-val {
  color: #1f2937;
}

/* ── 卡片底部动作条 ───────────────────────────────────────── */
.action-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(0, 0, 0, 0.05);
  flex-wrap: wrap;
}

.oversized-footer {
  border-top-color: rgba(253, 230, 138, 0.6);
}

.failed-footer {
  border-top-color: rgba(254, 202, 202, 0.6);
}

.footer-left {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.footer-right {
  display: flex;
  align-items: center;
  gap: 6px;
}

.retry-counter {
  font-size: 12px;
  color: #b91c1c;
  margin-right: 4px;
}

/* ── 按钮样式体系 ─────────────────────────────────────────── */
.btn-amber-solid {
  padding: 6px 13px;
  background: #f59e0b;
  color: #ffffff;
  font-size: 12.5px;
  font-weight: 700;
  border: 0;
  border-radius: var(--radius-sm, 6px);
  cursor: pointer;
  box-shadow: 0 1px 2px rgba(217, 119, 6, 0.2);
  transition: all 0.15s ease;
}

.btn-amber-solid:hover:not(:disabled) {
  background: #d97706;
}

.btn-amber-outline {
  padding: 6px 13px;
  background: #ffffff;
  border: 1px solid #fcd34d;
  color: #78350f;
  font-size: 12.5px;
  font-weight: 600;
  border-radius: var(--radius-sm, 6px);
  cursor: pointer;
  transition: all 0.15s ease;
}

.btn-amber-outline:hover:not(:disabled) {
  background: #fef3c7;
}

.btn-red-solid {
  padding: 6px 14px;
  background: #dc2626;
  color: #ffffff;
  font-size: 12.5px;
  font-weight: 700;
  border: 0;
  border-radius: var(--radius-sm, 6px);
  cursor: pointer;
  box-shadow: 0 1px 2px rgba(220, 38, 38, 0.2);
  transition: all 0.15s ease;
}

.btn-red-solid:hover:not(:disabled) {
  background: #b91c1c;
}

.btn-red-outline {
  padding: 6px 12px;
  background: #ffffff;
  border: 1px solid #fecaca;
  color: #dc2626;
  font-size: 12.5px;
  font-weight: 500;
  border-radius: var(--radius-sm, 6px);
  cursor: pointer;
  transition: all 0.15s ease;
}

.btn-red-outline:hover:not(:disabled) {
  background: #fef2f2;
}

.btn-gray-outline {
  padding: 6px 12px;
  background: #ffffff;
  border: 1px solid rgba(0, 0, 0, 0.1);
  color: #374151;
  font-size: 12.5px;
  font-weight: 500;
  border-radius: var(--radius-sm, 6px);
  cursor: pointer;
  transition: all 0.15s ease;
}

.btn-gray-outline:hover:not(:disabled) {
  background: #f3f4f6;
  color: #111827;
}

.btn-compact-clear {
  padding: 3px 9px;
  font-size: 12px;
  border-radius: var(--radius-sm, 6px);
}

.btn-compact-clear:hover:not(:disabled) {
  background: #fef2f2;
  color: #dc2626;
  border-color: #fecaca;
}

button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* ── 紧凑单行排队卡片 (Pending / Assigned) ───────────────── */
.compact-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.compact-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}

.compact-right {
  display: flex;
  align-items: center;
  gap: 6px;
}

.compact-dot {
  width: 7px;
  height: 7px;
  border-radius: var(--radius-full, 9999px);
  flex-shrink: 0;
}

.dot-amber {
  background: #fbbf24;
}

.dot-gray {
  background: #d1d5db;
}

.compact-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.compact-title {
  font-size: 13.5px;
  font-weight: 700;
  color: #1f2937;
  line-height: 1.3;
}

.compact-sub {
  font-size: 12px;
  color: #9ca3af;
  margin-top: 1px;
}

.pending-warn-box {
  font-size: 12px;
  color: #b45309;
  background: #fffbeb;
  padding: 5px 10px;
  border-radius: var(--radius-sm, 6px);
  line-height: 1.3;
}

/* ── 封面生成动画 ─────────────────────────────────────────── */
.preparing-rail {
  position: relative;
  height: 4px;
  overflow: hidden;
  border-radius: var(--radius-full, 9999px);
  background: #e5e7eb;
}

.rail-fill {
  position: absolute;
  top: 0;
  left: 0;
  width: 40%;
  height: 100%;
  border-radius: var(--radius-full, 9999px);
  background: #28995a;
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

/* ── 选择框 ───────────────────────────────────────────────── */
.pick {
  display: inline-flex;
  align-items: center;
  cursor: pointer;
  flex-shrink: 0;
}

.pick input {
  width: 14px;
  height: 14px;
  margin: 0;
  accent-color: #28995a;
  cursor: pointer;
}
</style>

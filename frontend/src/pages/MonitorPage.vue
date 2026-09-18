<script setup lang="ts">
/**
 * @file MonitorPage.vue
 * @description 监控控制台主页 (Monitor Dashboard)
 *
 * 核心架构：
 * 1. 【顶层系统遥测带 (Telemetry Ribbon)】：实时聚合计算当前总吞吐、传输中任务数、在线 Worker 比率与待发队列积压；
 * 2. 【主工作区双列异步布局】：左侧 340px Worker 管理列 + 右侧 Flex-1 宽幅传输通道，消除文件名截断痛点；
 * 3. 【Session 授权弹窗宿主】：通过 Teleport 挂载全局毛玻璃模态窗，解耦业务交互。
 */

import { computed, ref, watch } from "vue";
import WorkerPanel from "../components/WorkerPanel.vue";
import ProgressPanel from "../components/ProgressPanel.vue";
import SessionPanel from "../components/SessionPanel.vue";
import type { WorkerSnapshot } from "../types";

// ── 响应式状态定义 ─────────────────────────────────────────────

/** 控制添加 Session 模态弹窗的显示与隐藏 */
const showSessionForm = ref(false);

/** 由子组件 WorkerPanel 派发的最新 Worker 快照数组 */
const workers = ref<WorkerSnapshot[]>([]);

/** 由子组件 ProgressPanel 派发的实时进度聚合统计（当前总速度、在传任务数） */
const progressSummary = ref({ totalSpeed: 0, inFlightCount: 0 });

// ── 弹窗交互控制 ───────────────────────────────────────────────

function openSessionForm(): void {
  showSessionForm.value = true;
}

function closeSessionForm(): void {
  showSessionForm.value = false;
}

// ── 子组件数据同步事件处理器 ───────────────────────────────────

/** 接收 Worker 列表更新，用于顶部遥测卡片计算在线数与队列深度 */
function onUpdateWorkers(list: WorkerSnapshot[]): void {
  workers.value = list;
}

/** 接收进度统计更新，用于顶部遥测卡片展示全局吞吐速率 */
function onUpdateProgress(summary: { totalSpeed: number; inFlightCount: number }): void {
  progressSummary.value = summary;
}

// ── 遥测指标计算衍生量 (Computed Telemetry) ────────────────────

/** 当前就绪且正接受任务的活跃 Worker 数量 */
const activeWorkersCount = computed(() =>
  workers.value.filter((w) => w.enabled && w.running && w.accepting).length,
);

/** 已挂载的 Worker 节点总数 */
const totalWorkersCount = computed(() => workers.value.length);

/** 所有 Worker 节点内部队列等待分配的文件总数 */
const totalQueueCount = computed(() =>
  workers.value.reduce((acc, w) => acc + (w.queue_size || 0), 0),
);

/**
 * 格式化传输速率为人类可读字符串
 * @param bytesPerSec 字节/秒
 */
function formatSpeed(bytesPerSec: number): string {
  if (bytesPerSec <= 0) return "0 B/s";
  if (bytesPerSec < 1024) return `${Math.round(bytesPerSec)} B/s`;
  if (bytesPerSec < 1024 * 1024) return `${(bytesPerSec / 1024).toFixed(1)} KB/s`;
  if (bytesPerSec < 1024 * 1024 * 1024) return `${(bytesPerSec / (1024 * 1024)).toFixed(1)} MB/s`;
  return `${(bytesPerSec / (1024 * 1024 * 1024)).toFixed(2)} GB/s`;
}

// 弹窗展开时锁定 body 滚动条，防止页面背景滚动穿透
watch(showSessionForm, (open) => {
  document.body.style.overflow = open ? "hidden" : "";
});
</script>

<template>
  <div class="monitor-container">
    <!-- ── 顶部一体化系统遥测带 (Integrated Telemetry Ribbon) ── -->
    <section class="telemetry-ribbon">
      <!-- 实时总吞吐 -->
      <div class="telemetry-cell">
        <div class="cell-icon speed-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="17" height="17">
            <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>
        <div class="cell-data">
          <span class="cell-label">实时总吞吐</span>
          <span class="cell-value" :class="{ 'highlight-speed': progressSummary.totalSpeed > 0 }">
            {{ formatSpeed(progressSummary.totalSpeed) }}
          </span>
        </div>
      </div>

      <div class="telemetry-divider"></div>

      <!-- 正在传输任务数 -->
      <div class="telemetry-cell">
        <div class="cell-icon task-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="17" height="17">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>
        <div class="cell-data">
          <span class="cell-label">正在传输</span>
          <span class="cell-value" :class="{ 'highlight-task': progressSummary.inFlightCount > 0 }">
            {{ progressSummary.inFlightCount }} <span class="cell-unit">任务</span>
          </span>
        </div>
      </div>

      <div class="telemetry-divider"></div>

      <!-- 可用 Worker 比率 -->
      <div class="telemetry-cell">
        <div class="cell-icon worker-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="17" height="17">
            <rect x="2" y="7" width="20" height="14" rx="2" ry="2"/>
            <path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>
          </svg>
        </div>
        <div class="cell-data">
          <span class="cell-label">可用节点</span>
          <span class="cell-value">
            {{ activeWorkersCount }} <span class="cell-unit">/ {{ totalWorkersCount }}</span>
          </span>
        </div>
      </div>

      <div class="telemetry-divider"></div>

      <!-- 待发队列积压深度 -->
      <div class="telemetry-cell">
        <div class="cell-icon queue-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="17" height="17">
            <circle cx="12" cy="12" r="10"/>
            <polyline points="12 6 12 12 16 14"/>
          </svg>
        </div>
        <div class="cell-data">
          <span class="cell-label">队列积压</span>
          <span class="cell-value" :class="{ 'warn-queue': totalQueueCount > 0 }">
            {{ totalQueueCount }} <span class="cell-unit">待传</span>
          </span>
        </div>
      </div>
    </section>

    <!-- ── 主工作区：左侧 Worker 节点列表 + 右侧宽幅实时传输通道 ── -->
    <main class="workspace-layout">
      <!-- 左栏：Worker 管理 -->
      <aside class="worker-column">
        <WorkerPanel @add="openSessionForm" @update-workers="onUpdateWorkers" />
      </aside>

      <!-- 右栏：实时文件进度瀑布流 -->
      <section class="progress-column">
        <ProgressPanel @update-summary="onUpdateProgress" />
      </section>
    </main>
  </div>

  <!-- Session 授权创建弹窗 -->
  <Teleport to="body">
    <div
      v-if="showSessionForm"
      class="session-overlay"
      @click.self="closeSessionForm"
    >
      <div class="session-modal" @click.stop>
        <SessionPanel @close="closeSessionForm" />
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.monitor-container {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 8px 0 40px;
}

/* ── 一体式系统遥测带 (Integrated Telemetry Ribbon) ── */
.telemetry-ribbon {
  display: flex;
  align-items: center;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 16px;
  box-shadow: var(--shadow);
  padding: 12px 20px;
}

.telemetry-cell {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 4px 12px;
}

.telemetry-divider {
  width: 1px;
  height: 32px;
  background: var(--border-light);
  flex-shrink: 0;
}

.cell-icon {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.speed-icon {
  background: #ebf6f0;
  color: var(--accent);
}

.task-icon {
  background: #f0f7f2;
  color: #2b8b57;
}

.worker-icon {
  background: #f4f6f4;
  color: #4a614e;
}

.queue-icon {
  background: #fcf6eb;
  color: var(--warn);
}

.cell-data {
  display: flex;
  flex-direction: column;
}

.cell-label {
  font-size: 11.5px;
  color: var(--text-secondary);
  font-weight: 500;
  margin-bottom: 2px;
}

.cell-value {
  font-size: 16.5px;
  font-weight: 700;
  color: var(--text);
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em;
}

.cell-unit {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
}

.highlight-speed {
  color: var(--accent);
}

.highlight-task {
  color: #24804e;
}

.warn-queue {
  color: var(--warn);
}

/* ── 主工作布局 (两列异步视界) ── */
.workspace-layout {
  display: flex;
  gap: 18px;
  align-items: flex-start;
}

.worker-column {
  width: 340px;
  flex-shrink: 0;
}

.progress-column {
  flex: 1;
  min-width: 0;
}

@media (max-width: 980px) {
  .telemetry-ribbon {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    padding: 16px;
  }
  .telemetry-divider {
    display: none;
  }
  .workspace-layout {
    flex-direction: column;
  }
  .worker-column {
    width: 100%;
  }
}

@media (max-width: 600px) {
  .telemetry-ribbon {
    grid-template-columns: 1fr;
  }
}
</style>

<script setup lang="ts">
/**
 * @file MonitorPage.vue
 * @description 监控控制台主页 (Monitor Dashboard)
 *
 * 核心架构：
 * 1. 【顶层系统遥测带 (Telemetry Ribbon)】：成功数、传输中任务数、在线 Worker 比率与待发队列积压；
 * 2. 【主工作区】：左侧 Worker 列 + 右侧四列任务看板；
 * 3. 【Session 授权弹窗宿主】：通过 Teleport 挂载全局毛玻璃模态窗，解耦业务交互。
 */

import { computed, ref, watch } from "vue";
import WorkerPanel from "../components/WorkerPanel.vue";
import TaskBoard from "../components/TaskBoard.vue";
import SessionPanel from "../components/SessionPanel.vue";
import { useTaskBoard } from "../composables/useTaskBoard";
import type { WorkerSnapshot } from "../types";

// ── 响应式状态定义 ─────────────────────────────────────────────

/** 控制添加 Session 模态弹窗的显示与隐藏 */
const showSessionForm = ref(false);

/** 由子组件 WorkerPanel 派发的最新 Worker 快照数组 */
const workers = ref<WorkerSnapshot[]>([]);

const { items: boardItems, inFlightCount, queueCount, successCount } = useTaskBoard();

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

// ── 遥测指标计算衍生量 (Computed Telemetry) ────────────────────

/** 当前就绪且正接受任务的活跃 Worker 数量 */
const activeWorkersCount = computed(() =>
  workers.value.filter((w) => w.enabled && w.running && w.accepting).length,
);

/** 已挂载的 Worker 节点总数 */
const totalWorkersCount = computed(() => workers.value.length);

// 弹窗展开时锁定 body 滚动条，防止页面背景滚动穿透
watch(showSessionForm, (open) => {
  document.body.style.overflow = open ? "hidden" : "";
});
</script>

<template>
  <div class="monitor-container">
    <!-- ── 顶部一体化系统遥测带 (Integrated Telemetry Ribbon) ── -->
    <section class="telemetry-ribbon">
      <div class="telemetry-cell">
        <div class="cell-icon success-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="17" height="17">
            <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" stroke-linecap="round" stroke-linejoin="round"/>
            <polyline points="22 4 12 14.01 9 11.01" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>
        <div class="cell-data">
          <span class="cell-label">上传成功</span>
          <span class="cell-value" :class="{ 'highlight-task': successCount > 0 }">
            {{ successCount }} <span class="cell-unit">条</span>
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
          <span class="cell-value" :class="{ 'highlight-task': inFlightCount > 0 }">
            {{ inFlightCount }} <span class="cell-unit">任务</span>
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
          <span class="cell-value" :class="{ 'warn-queue': queueCount > 0 }">
            {{ queueCount }} <span class="cell-unit">待处理</span>
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

      <section class="progress-column">
        <TaskBoard :items="boardItems" />
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
  padding: 8px 0 0;
  flex: 1;
  min-height: 0;
  height: 100%;
  overflow: hidden;
}

/* ── 一体式系统遥测带 (Integrated Telemetry Ribbon) ── */
.telemetry-ribbon {
  display: flex;
  align-items: center;
  flex-shrink: 0;
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

.success-icon {
  background: #ebf6f0;
  color: var(--ok);
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
  align-items: stretch;
  flex: 1;
  min-height: 0;
}

.worker-column {
  width: 280px;
  flex-shrink: 0;
  height: 100%;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
}

.progress-column {
  flex: 1;
  min-width: 0;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.progress-column > * {
  flex: 1;
  min-height: 0;
  min-width: 0;
  height: 100%;
}

@media (max-width: 1100px) {
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
    flex: 0 0 36vh;
    max-height: 36vh;
    height: auto;
  }
  .progress-column {
    flex: 1;
    min-height: 0;
  }
}

@media (max-width: 600px) {
  .telemetry-ribbon {
    grid-template-columns: 1fr;
  }
}
</style>

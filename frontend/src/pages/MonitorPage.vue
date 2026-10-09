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

import { computed, onDeactivated, ref, watch } from "vue";
import WorkerPanel from "../components/WorkerPanel.vue";
import TaskBoard from "../components/TaskBoard.vue";
import SessionPanel from "../components/SessionPanel.vue";
import { fetchSuccessPage } from "../api";
import type { SuccessRecord } from "../api";
import { useTaskBoard } from "../composables/useTaskBoard";
import { useUnmatchedPolling } from "../composables/useUnmatched";
import { formatBytes } from "../format";
import type { WorkerSnapshot } from "../types";

defineOptions({ name: "MonitorPage" });

// ── 响应式状态定义 ─────────────────────────────────────────────

/** 控制添加 Session 模态弹窗的显示与隐藏 */
const showSessionForm = ref(false);

/** 移动端当前选中的主视图 tab ('board' | 'workers') */
const mobileTab = ref<"board" | "workers">("board");

/** 由子组件 WorkerPanel 派发的最新 Worker 快照数组 */
const workers = ref<WorkerSnapshot[]>([]);

const { items: boardItems, inFlightCount, queueCount, successToday } = useTaskBoard();

const SUCCESS_SCOPE_KEY = "uploader.success-scope";
const { files: unmatchedFiles } = useUnmatchedPolling();
const unmatchedCount = computed(() => unmatchedFiles.value.length);

const successScope = ref<"today" | "all">(loadSuccessScope());
const successCount = computed(() => successToday.value);
const successOpen = ref(false);
const successPage = ref(1);
const successPageSize = 50;
const successTotalRows = ref(0);
const successItems = ref<SuccessRecord[]>([]);
const successLoading = ref(false);
const successPages = computed(() =>
  Math.max(1, Math.ceil(successTotalRows.value / successPageSize)),
);

function loadSuccessScope(): "today" | "all" {
  return localStorage.getItem(SUCCESS_SCOPE_KEY) === "all" ? "all" : "today";
}

onDeactivated(() => {
  showSessionForm.value = false;
  successOpen.value = false;
});

function onSuccessScopeChange(value: "today" | "all"): void {
  successScope.value = value;
  localStorage.setItem(SUCCESS_SCOPE_KEY, value);
  if (successOpen.value) {
    successPage.value = 1;
    void loadSuccess();
  }
}

async function loadSuccess(): Promise<void> {
  successLoading.value = true;
  try {
    const data = await fetchSuccessPage(successScope.value, successPage.value, successPageSize);
    successItems.value = data.items;
    successTotalRows.value = data.total;
    successPage.value = data.page;
  } catch {
    successItems.value = [];
    successTotalRows.value = 0;
  } finally {
    successLoading.value = false;
  }
}

function openSuccess(): void {
  successOpen.value = true;
  successPage.value = 1;
  void loadSuccess();
}

function closeSuccess(): void {
  successOpen.value = false;
}

async function shiftSuccessPage(step: number): Promise<void> {
  const next = successPage.value + step;
  if (next < 1 || next > successPages.value) return;
  successPage.value = next;
  await loadSuccess();
}

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
watch([showSessionForm, successOpen], ([sessionOpen, historyOpen]) => {
  document.body.style.overflow = sessionOpen || historyOpen ? "hidden" : "";
});

watch(successCount, () => {
  if (successOpen.value && successPage.value === 1) void loadSuccess();
});
</script>

<template>
  <div class="monitor-page">
  <div class="monitor-container">
    <p v-if="unmatchedCount > 0" class="unmatched-banner">
      {{ unmatchedCount }} 个文件未命中路由，未进入上传队列。到设置的「投递」里为路径指定群。
    </p>
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
          <span class="cell-label">今日成功</span>
          <button type="button" class="success-open" :aria-expanded="successOpen" @click="openSuccess">
            <span class="cell-value" :class="{ 'highlight-task': successCount > 0 }">
              {{ successCount }} <span class="cell-unit">条</span>
            </span>
            <span class="view-mark">查看</span>
          </button>
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

    <!-- ── 移动端分段视图切换 (仅在窄屏呈现) ── -->
    <div class="mobile-view-tabs" role="tablist" aria-label="工作区视图切换">
      <button
        type="button"
        role="tab"
        :aria-selected="mobileTab === 'board'"
        class="mobile-view-btn"
        :class="{ active: mobileTab === 'board' }"
        @click="mobileTab = 'board'"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
          <rect x="3" y="3" width="7" height="7" rx="1.5"/>
          <rect x="14" y="3" width="7" height="7" rx="1.5"/>
          <rect x="3" y="14" width="7" height="7" rx="1.5"/>
          <rect x="14" y="14" width="7" height="7" rx="1.5"/>
        </svg>
        <span>任务看板</span>
        <span v-if="boardItems.length" class="view-badge">{{ boardItems.length }}</span>
      </button>
      <button
        type="button"
        role="tab"
        :aria-selected="mobileTab === 'workers'"
        class="mobile-view-btn"
        :class="{ active: mobileTab === 'workers' }"
        @click="mobileTab = 'workers'"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
          <rect x="2" y="7" width="20" height="14" rx="2" ry="2"/>
          <path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>
        </svg>
        <span>Worker 节点</span>
        <span v-if="totalWorkersCount" class="view-badge">{{ activeWorkersCount }}/{{ totalWorkersCount }}</span>
      </button>
    </div>

    <!-- ── 主工作区：左侧 Worker 节点列表 + 右侧宽幅实时传输通道 ── -->
    <main class="workspace-layout" :class="`show-${mobileTab}`">
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

  <Teleport to="body">
    <div v-if="successOpen" class="session-overlay" @click.self="closeSuccess">
      <div class="session-modal success-modal" role="dialog" aria-label="上传成功" @click.stop>
        <header class="success-head">
          <h2>上传成功</h2>
          <div class="success-switch">
            <button
              type="button"
              :class="{ on: successScope === 'today' }"
              @click="onSuccessScopeChange('today')"
            >
              今日
            </button>
            <button
              type="button"
              :class="{ on: successScope === 'all' }"
              @click="onSuccessScopeChange('all')"
            >
              累计
            </button>
          </div>
        </header>
        <p v-if="successLoading" class="success-empty">正在读取</p>
        <p v-else-if="successItems.length === 0" class="success-empty">没有成功记录</p>
        <ul v-else class="success-list">
          <li v-for="item in successItems" :key="item.id">
            <span class="success-name" :title="item.file_name">{{ item.file_name }}</span>
            <span v-if="item.part_label" class="success-part">{{ item.part_label }}</span>
            <span class="success-meta">
              <template v-if="item.file_size > 0">{{ formatBytes(item.file_size) }}</template>
              <template v-if="item.folder_name"> · {{ item.folder_name }}</template>
              <template v-if="item.finished_at"> · {{ item.finished_at }}</template>
            </span>
          </li>
        </ul>
        <footer class="success-pager">
          <button type="button" :disabled="successPage <= 1 || successLoading" @click="shiftSuccessPage(-1)">
            上一页
          </button>
          <span>第 {{ successPage }} / {{ successPages }} 页</span>
          <button
            type="button"
            :disabled="successPage >= successPages || successLoading"
            @click="shiftSuccessPage(1)"
          >
            下一页
          </button>
        </footer>
      </div>
    </div>
  </Teleport>
  </div>
</template>

<style scoped>
.monitor-page {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  height: 100%;
  overflow: hidden;
}

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

.unmatched-banner {
  margin: 0;
  padding: 10px 14px;
  border: 1px solid #f3d7a1;
  border-radius: 12px;
  background: #fdf5ea;
  color: var(--warn);
  font-size: 13px;
  flex-shrink: 0;
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

.scope-select {
  width: 108px;
}

.scope-select :deep(.el-select__wrapper) {
  padding: 0 8px;
  min-height: 22px;
  box-shadow: none;
  background: transparent;
}

.scope-select :deep(.el-select__selected-item) {
  font-size: 11.5px;
  font-weight: 500;
  color: var(--text-secondary);
}

.success-line {
  display: flex;
  align-items: center;
}

.success-open {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
  color: inherit;
}

.view-mark {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 1px 7px;
  border: 1px solid var(--border);
  border-radius: 9999px;
  color: var(--text-secondary);
  font-size: 11px;
  line-height: 1.6;
}

.view-mark::before {
  content: "";
  width: 10px;
  height: 8px;
  border: 1.4px solid currentColor;
  border-radius: 1px;
  box-shadow: inset 0 -2px 0 currentColor;
}

.success-open:hover .view-mark {
  color: var(--text);
  border-color: rgba(0, 0, 0, 0.16);
}

.success-modal {
  width: min(640px, 100%);
  padding: 16px 16px 12px;
  background: var(--surface);
}

.success-switch {
  display: flex;
  background: #f3f5f3;
  border-radius: 999px;
  padding: 3px;
}

.success-switch button {
  border: 0;
  background: transparent;
  border-radius: 999px;
  padding: 4px 12px;
  font-size: 12.5px;
  color: var(--text-secondary);
  cursor: pointer;
}

.success-switch button.on {
  background: var(--surface);
  color: var(--text);
  font-weight: 650;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
}

.success-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.success-head h2 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}

.success-close,
.success-pager button {
  border: 1px solid var(--border);
  background: var(--surface);
  border-radius: 8px;
  padding: 2px 8px;
  font-size: 12px;
  cursor: pointer;
}

.success-close:disabled,
.success-pager button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.success-empty {
  margin: 8px 0;
  color: var(--text-secondary);
  font-size: 13px;
}

.success-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 280px;
  overflow: auto;
}

.success-list li {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  align-items: baseline;
  padding: 8px 0;
  border-top: 1px solid var(--border-light);
  font-size: 13px;
}

.success-name {
  font-weight: 550;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.success-part {
  color: var(--text-secondary);
  font-size: 12px;
}

.success-meta {
  margin-left: auto;
  color: var(--text-secondary);
  font-size: 12px;
}

.success-pager {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-secondary);
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

/* ── 移动端分段视图切换器 ── */
.mobile-view-tabs {
  display: none;
}

.mobile-view-btn {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 12px;
  border: none;
  border-radius: 9px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.mobile-view-btn.active {
  background: var(--surface);
  color: var(--text);
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}

.view-badge {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 9999px;
  background: var(--hover);
  color: var(--text-secondary);
}

.mobile-view-btn.active .view-badge {
  background: var(--accent-soft);
  color: var(--accent);
}

@media (max-width: 1100px) and (min-width: 769px) {
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

@media (max-width: 768px) {
  .monitor-page {
    height: auto;
    min-height: 0;
    overflow: visible;
  }

  .monitor-container {
    height: auto;
    overflow: visible;
    gap: 12px;
    padding: 0;
  }

  .unmatched-banner {
    padding: 8px 12px;
    font-size: 12px;
  }

  .telemetry-ribbon {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px;
    padding: 8px 10px;
    border-radius: 12px;
  }

  .telemetry-divider {
    display: none;
  }

  .telemetry-cell {
    padding: 3px 6px;
    gap: 8px;
  }

  .cell-icon {
    width: 28px;
    height: 28px;
    border-radius: 8px;
  }

  .cell-icon svg {
    width: 14px;
    height: 14px;
  }

  .cell-label {
    font-size: 10.5px;
    margin-bottom: 0;
  }

  .cell-value {
    font-size: 13.5px;
  }

  .cell-unit {
    font-size: 10.5px;
  }

  .scope-select {
    width: 86px;
  }

  .scope-select :deep(.el-select__wrapper) {
    min-height: 18px;
    padding: 0 4px;
  }

  .scope-select :deep(.el-select__selected-item) {
    font-size: 10.5px;
  }

  .mobile-view-tabs {
    display: flex;
    background: #eef2ef;
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 3px;
    gap: 4px;
    flex-shrink: 0;
  }

  .workspace-layout {
    flex-direction: column;
    height: auto;
    flex: 1;
    min-height: 0;
  }

  .workspace-layout.show-board .worker-column {
    display: none;
  }

  .workspace-layout.show-workers .progress-column {
    display: none;
  }

  .worker-column {
    width: 100%;
    height: auto;
    max-height: none;
    flex: 1;
    overflow: visible;
  }

  .progress-column {
    width: 100%;
    height: auto;
    flex: 1;
    overflow: visible;
  }
}
</style>

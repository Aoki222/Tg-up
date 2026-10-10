<script setup lang="ts">
/**
 * 单栏任务流：全部 / 正在上传 / 过大 / 失败 / 排队。
 * 排队包含封面生成和等待上传。过大、失败才出现批量菜单。
 */
import { computed, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  deleteAllFailedTasks,
  deleteAllOversizedTasks,
  deleteFailedTask,
  deleteSelectedFailedTasks,
  deleteSelectedOversizedTasks,
  continueSliceTask,
  dispatchOversizedBatch,
  dispatchOversizedTask,
  retryAllFailedTasks,
  retryBoardTask,
  sliceOversizedBatch,
  sliceOversizedTask,
} from "../api";
import type { SkippedTask } from "../api";
import type { BoardTask } from "../types";
import TaskCard from "./TaskCard.vue";

const props = defineProps<{ items: BoardTask[] }>();

const retryingId = ref<number | null>(null);
const dispatchingId = ref<number | null>(null);
const slicingId = ref<number | null>(null);
const retryingAll = ref(false);
const clearing = ref(false);
const oversizedActing = ref(false);
const selectedIds = ref<Set<number>>(new Set());
const oversizedIds = ref<Set<number>>(new Set());
type BoardTab = "all" | "uploading" | "oversized" | "failed" | "queue";
const boardTab = ref<BoardTab>("all");

function closeBatch(): void {}

const buckets = computed(() => {
  const preparing: BoardTask[] = [];
  const pending: BoardTask[] = [];
  const uploading: BoardTask[] = [];
  const oversized: BoardTask[] = [];
  const failed: BoardTask[] = [];
  for (const item of props.items) {
    if (item.status === "preparing") preparing.push(item);
    else if (item.status === "pending") pending.push(item);
    else if (item.status === "assigned" || item.status === "uploading") uploading.push(item);
    else if (item.status === "oversized") oversized.push(item);
    else if (item.status === "failed") failed.push(item);
  }
  return { preparing, pending, uploading, oversized, failed };
});

const queueItems = computed(() => [...buckets.value.preparing, ...buckets.value.pending]);
const activeTotal = computed(
  () =>
    buckets.value.uploading.length +
    buckets.value.oversized.length +
    buckets.value.failed.length +
    queueItems.value.length,
);
interface SectionMeta {
  key: "uploading" | "oversized" | "failed" | "queue";
  title: string;
  sub: string;
  dotClass: string;
  items: BoardTask[];
}

const allSections = computed<SectionMeta[]>(() => [
  {
    key: "uploading",
    title: "正在上传看板",
    sub: "活跃中",
    dotClass: "dot-uploading",
    items: buckets.value.uploading,
  },
  {
    key: "oversized",
    title: "过大待处理看板",
    sub: "超过 2GB 限制",
    dotClass: "dot-oversized",
    items: buckets.value.oversized,
  },
  {
    key: "failed",
    title: "失败与重试看板",
    sub: "需人工介入或重试",
    dotClass: "dot-failed",
    items: buckets.value.failed,
  },
  {
    key: "queue",
    title: "排队就绪看板",
    sub: "待 Worker 调度",
    dotClass: "dot-queue",
    items: queueItems.value,
  },
]);

function sectionCountLabel(section: SectionMeta): string {
  if (section.key === "uploading") return `${section.items.length} 个任务并发`;
  if (section.key === "oversized") return `${section.items.length} 个任务待裁决`;
  if (section.key === "failed") return `${section.items.length} 个任务中断`;
  return `${section.items.length} 个任务就绪`;
}

const streamSections = computed(() => {
  if (boardTab.value === "all") {
    return allSections.value.filter((section) => section.items.length > 0);
  }
  return allSections.value.filter((section) => section.key === boardTab.value);
});
async function onRetry(id: number): Promise<void> {
  // 调用后端将单条 failed 任务重新置为 pending。
  if (retryingId.value !== null || retryingAll.value) return;
  retryingId.value = id;
  try {
    const status = await retryBoardTask(id);
    ElMessage.success(status === "oversized" ? "超过 2GB，已放到过大" : "已重新排队");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "重试失败");
  } finally {
    retryingId.value = null;
  }
}

async function onRetryAll(): Promise<void> {
  // 批量重试失败任务，并把缺失文件数量反馈给用户。
  closeBatch();
  if (retryingAll.value || retryingId.value !== null || buckets.value.failed.length === 0) return;
  retryingAll.value = true;
  try {
    const result = await retryAllFailedTasks();
    const notes: string[] = [];
    if (result.retried > 0) notes.push(`已重新排队 ${result.retried} 个`);
    if (result.parked > 0) notes.push(`${result.parked} 个超过 2GB，已留在过大`);
    if (result.skipped > 0) notes.push(`跳过 ${result.skipped} 个`);
    if (notes.length === 0) {
      ElMessage.info("没有可重试的任务");
    } else if (result.retried === 0 && result.parked === 0) {
      ElMessage.warning(`文件不存在，已跳过 ${result.skipped} 个`);
    } else {
      ElMessage.success(notes.join("，"));
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "全部重试失败");
  } finally {
    retryingAll.value = false;
  }
}

const selectedCount = computed(() => selectedIds.value.size);
const oversizedCount = computed(() => oversizedIds.value.size);
const failedBusy = computed(
  () =>
    retryingAll.value ||
    retryingId.value !== null ||
    clearing.value ||
    dispatchingId.value !== null ||
    slicingId.value !== null ||
    oversizedActing.value,
);

function toggleSelect(id: number): void {
  // 切换失败任务的批量删除选择状态。
  const next = new Set(selectedIds.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  selectedIds.value = next;
}

function toggleOversized(id: number): void {
  const next = new Set(oversizedIds.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  oversizedIds.value = next;
}

function forgetSelected(ids: number[]): void {
  // 删除任务后同步移除本地已选 ID，避免残留选择影响按钮状态。
  const next = new Set(selectedIds.value);
  for (const id of ids) next.delete(id);
  selectedIds.value = next;
  const oversized = new Set(oversizedIds.value);
  for (const id of ids) oversized.delete(id);
  oversizedIds.value = oversized;
}

function reportBatch(doneLabel: string, done: number, skipped: SkippedTask[]): void {
  const head = done > 0 ? `${doneLabel} ${done} 个` : "";
  const tail =
    skipped.length > 0
      ? `跳过 ${skipped.length} 个：${skipped
          .slice(0, 3)
          .map((item) => `${item.file_name || "文件"} ${item.reason}`)
          .join("；")}`
      : "";
  const text = [head, tail].filter(Boolean).join("，");
  if (!text) {
    ElMessage.info("没有可处理的文件");
    return;
  }
  if (skipped.length > 0) ElMessage.warning(text);
  else ElMessage.success(text);
}

async function onDispatch(id: number): Promise<void> {
  // 过大文件只交给个人号。没有个人号时后端返回「没有可用的个人账号」。
  if (failedBusy.value) return;
  dispatchingId.value = id;
  try {
    await dispatchOversizedTask(id);
    ElMessage.success("已交给个人号");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "无法交给个人号");
  } finally {
    dispatchingId.value = null;
  }
}

async function onSlice(id: number): Promise<void> {
  if (failedBusy.value) return;
  const task = props.items.find((item) => item.id === id);
  const min = task?.slice_min_parts && task.slice_min_parts > 1 ? task.slice_min_parts : 2;
  let parts = min;
  try {
    const result = await ElMessageBox.prompt(
      `至少 ${min} 段，最多 30 段。按整段时长平均切，原文件留到全部传完。`,
      "切片后上传",
      {
        inputValue: String(min),
        inputPattern: /^[1-9]\d*$/,
        inputErrorMessage: "请输入段数",
        confirmButtonText: "开始切片",
        cancelButtonText: "取消",
      },
    );
    parts = Number(result.value);
  } catch {
    return;
  }
  if (parts < min || parts > 30) {
    ElMessage.error(`段数至少为 ${min}，最多 30`);
    return;
  }
  slicingId.value = id;
  try {
    await sliceOversizedTask(id, parts);
    ElMessage.success("已加入切片");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "无法切片");
  } finally {
    slicingId.value = null;
  }
}

async function onContinueSlice(id: number): Promise<void> {
  if (failedBusy.value) return;
  slicingId.value = id;
  try {
    await continueSliceTask(id);
    ElMessage.success("已从失败的那段继续");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "无法继续");
  } finally {
    slicingId.value = null;
  }
}

async function onRemove(id: number): Promise<void> {
  // 删除单条失败记录；只删除数据库记录，不触碰本地文件。
  if (failedBusy.value) return;
  clearing.value = true;
  try {
    await deleteFailedTask(id);
    forgetSelected([id]);
    ElMessage.success("已清除");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "清除失败");
  } finally {
    clearing.value = false;
  }
}

async function onRemoveSelected(): Promise<void> {
  // 将选中的失败任务 ID 一次提交给后端删除。
  closeBatch();
  const ids = [...selectedIds.value];
  if (failedBusy.value || ids.length === 0) return;
  clearing.value = true;
  try {
    const deleted = await deleteSelectedFailedTasks(ids);
    selectedIds.value = new Set();
    ElMessage.success(deleted ? `已清除 ${deleted} 条` : "没有可清除的任务");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "清除失败");
  } finally {
    clearing.value = false;
  }
}

async function onRemoveAll(): Promise<void> {
  // 二次确认后删除全部失败记录，并保留磁盘文件。
  closeBatch();
  if (failedBusy.value || buckets.value.failed.length === 0) return;
  try {
    await ElMessageBox.confirm("将从数据库删除全部失败记录，本地文件不会动。", "全部清除", {
      type: "warning",
      confirmButtonText: "清除",
      cancelButtonText: "取消",
      confirmButtonClass: "el-button--danger",
    });
  } catch {
    return;
  }
  clearing.value = true;
  try {
    const deleted = await deleteAllFailedTasks();
    selectedIds.value = new Set();
    ElMessage.success(deleted ? `已清除 ${deleted} 条` : "没有可清除的任务");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "清除失败");
  } finally {
    clearing.value = false;
  }
}

async function onSliceBatch(ids: number[]): Promise<void> {
  closeBatch();
  if (failedBusy.value) return;
  oversizedActing.value = true;
  try {
    const result = await sliceOversizedBatch(ids);
    reportBatch("已加入切片", result.queued, result.skipped);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "无法切片");
  } finally {
    oversizedActing.value = false;
  }
}

async function onDispatchBatch(ids: number[]): Promise<void> {
  closeBatch();
  if (failedBusy.value) return;
  oversizedActing.value = true;
  try {
    const result = await dispatchOversizedBatch(ids);
    reportBatch("已交给个人号", result.assigned, result.skipped);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "无法交给个人号");
  } finally {
    oversizedActing.value = false;
  }
}

async function onRemoveOversizedSelected(): Promise<void> {
  closeBatch();
  const ids = [...oversizedIds.value];
  if (failedBusy.value || ids.length === 0) return;
  oversizedActing.value = true;
  try {
    const result = await deleteSelectedOversizedTasks(ids);
    oversizedIds.value = new Set();
    reportBatch("已清除", result.deleted, result.skipped);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "清除失败");
  } finally {
    oversizedActing.value = false;
  }
}

async function onRemoveOversizedAll(): Promise<void> {
  closeBatch();
  if (failedBusy.value || buckets.value.oversized.length === 0) return;
  try {
    await ElMessageBox.confirm("将从数据库删除全部过大记录，本地源文件不会动。", "全部清除", {
      type: "warning",
      confirmButtonText: "清除",
      cancelButtonText: "取消",
      confirmButtonClass: "el-button--danger",
    });
  } catch {
    return;
  }
  oversizedActing.value = true;
  try {
    const result = await deleteAllOversizedTasks();
    oversizedIds.value = new Set();
    reportBatch("已清除", result.deleted, result.skipped);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "清除失败");
  } finally {
    oversizedActing.value = false;
  }
}


</script>

<template>
  <section class="stream" aria-label="任务看板">
    <!-- 看板选项卡分类控制器与动作条 -->
    <div class="stream-tabs">
      <div class="tabs-group">
        <button
          type="button"
          class="tab-btn"
          :class="{ on: boardTab === 'all' }"
          @click="boardTab = 'all'"
        >
          <span>全部看板</span>
          <span class="tab-badge badge-all">{{ activeTotal }}</span>
        </button>
        <button
          type="button"
          class="tab-btn"
          :class="{ on: boardTab === 'uploading' }"
          @click="boardTab = 'uploading'"
        >
          <span class="status-dot dot-uploading"></span>
          <span>正在上传</span>
          <span class="tab-badge badge-uploading">{{ buckets.uploading.length }}</span>
        </button>
        <button
          type="button"
          class="tab-btn"
          :class="{ on: boardTab === 'oversized' }"
          @click="boardTab = 'oversized'"
        >
          <span class="status-dot dot-oversized"></span>
          <span>过大待处理</span>
          <span class="tab-badge badge-oversized">{{ buckets.oversized.length }}</span>
        </button>
        <button
          type="button"
          class="tab-btn"
          :class="{ on: boardTab === 'failed' }"
          @click="boardTab = 'failed'"
        >
          <span class="status-dot dot-failed"></span>
          <span>失败重试</span>
          <span class="tab-badge badge-failed">{{ buckets.failed.length }}</span>
        </button>
        <button
          type="button"
          class="tab-btn"
          :class="{ on: boardTab === 'queue' }"
          @click="boardTab = 'queue'"
        >
          <span class="status-dot dot-queue"></span>
          <span>排队就绪</span>
          <span class="tab-badge badge-queue">{{ queueItems.length }}</span>
        </button>
      </div>

      <!-- 右侧动作区与状态统计 -->
      <div class="tabs-actions">
        <!-- 全部看板模式：显示任务总数概览 -->
        <span v-if="boardTab === 'all'" class="summary-text font-mono">
          全部 {{ activeTotal }} 个活动任务流转中
        </span>

        <!-- 正在上传或排队模式：显示简要状态 -->
        <span v-else-if="boardTab === 'uploading'" class="summary-text font-mono">
          {{ buckets.uploading.length }} 个任务并发中
        </span>
        <span v-else-if="boardTab === 'queue'" class="summary-text font-mono">
          {{ queueItems.length }} 个任务等待 Worker
        </span>

        <!-- 过大看板模式：批量操作按钮群 -->
        <div v-else-if="boardTab === 'oversized'" class="action-btn-group">
          <button
            v-if="oversizedCount > 0"
            type="button"
            class="action-btn accent"
            :disabled="failedBusy"
            @click="onSliceBatch([...oversizedIds])"
          >
            切片选中 ({{ oversizedCount }})
          </button>
          <button
            type="button"
            class="action-btn"
            :disabled="failedBusy || buckets.oversized.length === 0"
            @click="onSliceBatch([])"
          >
            全部切片
          </button>
          <button
            type="button"
            class="action-btn"
            :disabled="failedBusy || buckets.oversized.length === 0"
            @click="onDispatchBatch([])"
          >
            全部交给个人号
          </button>
          <button
            v-if="oversizedCount > 0"
            type="button"
            class="action-btn danger-text"
            :disabled="failedBusy"
            @click="onRemoveOversizedSelected"
          >
            清除选中 ({{ oversizedCount }})
          </button>
          <button
            type="button"
            class="action-btn danger"
            :disabled="failedBusy || buckets.oversized.length === 0"
            @click="onRemoveOversizedAll"
          >
            全部清除
          </button>
        </div>

        <!-- 失败看板模式：重试与清除按钮群 -->
        <div v-else-if="boardTab === 'failed'" class="action-btn-group">
          <button
            type="button"
            class="action-btn danger-bg"
            :disabled="failedBusy || buckets.failed.length === 0"
            @click="onRetryAll"
          >
            {{ retryingAll ? "重试中" : "全部重试" }}
          </button>
          <button
            v-if="selectedCount > 0"
            type="button"
            class="action-btn danger-text"
            :disabled="failedBusy"
            @click="onRemoveSelected"
          >
            清除选中 ({{ selectedCount }})
          </button>
          <button
            type="button"
            class="action-btn danger"
            :disabled="failedBusy || buckets.failed.length === 0"
            @click="onRemoveAll"
          >
            全部清除
          </button>
        </div>
      </div>
    </div>

    <!-- 局部滑动任务卡片流 -->
    <div class="stream-scroll">
      <section
        v-for="section in streamSections"
        :key="section.key"
        class="stream-section"
      >
        <!-- 分组标题条 (在全部看板模式下展示完整分类头，以及单项分组快捷按钮) -->
        <div v-if="boardTab === 'all'" class="section-header">
          <div class="section-header-left">
            <span class="status-dot" :class="section.dotClass"></span>
            <span class="section-title">{{ section.title }}</span>
            <span class="section-sub" :class="section.key">· {{ section.sub }}</span>
          </div>

          <div class="section-header-right">
            <span class="section-count-badge font-mono" :class="section.key">
              {{ sectionCountLabel(section) }}
            </span>

            <!-- 在全部看板视图下，也直接支持过大和失败的直接快捷操作 -->
            <template v-if="section.key === 'oversized' && section.items.length > 0">
              <button
                type="button"
                class="mini-action-btn"
                :disabled="failedBusy"
                @click="onSliceBatch([])"
              >
                全部切片
              </button>
              <button
                type="button"
                class="mini-action-btn danger"
                :disabled="failedBusy"
                @click="onRemoveOversizedAll"
              >
                全部清除
              </button>
            </template>

            <template v-else-if="section.key === 'failed' && section.items.length > 0">
              <button
                type="button"
                class="mini-action-btn accent"
                :disabled="failedBusy"
                @click="onRetryAll"
              >
                {{ retryingAll ? "重试中" : "全部重试" }}
              </button>
              <button
                type="button"
                class="mini-action-btn danger"
                :disabled="failedBusy"
                @click="onRemoveAll"
              >
                全部清除
              </button>
            </template>
          </div>
        </div>

        <!-- 任务卡片列表 -->
        <div class="section-cards">
          <TaskCard
            v-for="task in section.items"
            :key="task.id"
            :task="task"
            :retrying="failedBusy"
            :dispatching="dispatchingId === task.id"
            :selectable="boardTab === section.key && (section.key === 'failed' || section.key === 'oversized')"
            :selected="section.key === 'oversized' ? oversizedIds.has(task.id) : selectedIds.has(task.id)"
            @retry="onRetry"
            @remove="onRemove"
            @toggle="section.key === 'oversized' ? toggleOversized($event) : toggleSelect($event)"
            @dispatch="onDispatch"
            @slice="onSlice"
            @continue="onContinueSlice"
          />
        </div>

        <!-- 列表空状态 (当特定分类无任务时) -->
        <div v-if="section.items.length === 0" class="empty-state-card">
          <span class="empty-dot"></span>
          <span>当前分类下暂无任务</span>
        </div>
      </section>

      <!-- 全局没有任何任务时的优雅空状态 -->
      <div v-if="streamSections.length === 0" class="empty-board-wrap">
        <div class="empty-board-inner">
          <div class="empty-icon-ring">
            <svg class="empty-svg" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <p class="empty-title">当前没有进行中的任务</p>
          <p class="empty-desc">监听目录中若有新文件落盘，将自动加入调度序列</p>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.stream {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg, 14px);
  box-shadow: var(--shadow);
  overflow: hidden;
}

.stream-tabs {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border-light, rgba(0, 0, 0, 0.06));
  flex-shrink: 0;
}

.tabs-group {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 3px;
  background: #f1f3f1;
  border-radius: var(--radius-md, 10px);
}

.tab-btn {
  border: 0;
  background: transparent;
  border-radius: var(--radius-sm, 6px);
  padding: 5px 9px;
  font-size: 11.5px;
  color: var(--text-secondary);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 5px;
  font-weight: 500;
  transition: all 0.15s ease;
}

.tab-btn:hover {
  color: var(--text);
}

.tab-btn.on {
  background: #ffffff;
  color: var(--text);
  font-weight: 700;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08);
}

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: var(--radius-full, 9999px);
  flex-shrink: 0;
}

.dot-uploading {
  background: var(--accent, #28995a);
}

.dot-oversized {
  background: #f59e0b;
}

.dot-failed {
  background: #ef4444;
}

.dot-queue {
  background: #9ca3af;
}

.tab-badge {
  padding: 1px 6px;
  border-radius: var(--radius-full, 9999px);
  font-size: 10px;
  font-weight: 700;
  font-family: ui-monospace, monospace;
}

.badge-all {
  background: #1f2937;
  color: #ffffff;
}

.badge-uploading {
  background: rgba(40, 153, 90, 0.12);
  color: #28995a;
}

.badge-oversized {
  background: #fef3c7;
  color: #b45309;
}

.badge-failed {
  background: #fee2e2;
  color: #b91c1c;
}

.badge-queue {
  background: #e5e7eb;
  color: #4b5563;
}

.tabs-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
  margin-left: auto;
}

.summary-text {
  font-size: 11.5px;
  color: var(--text-secondary);
  font-family: ui-monospace, monospace;
}

.action-btn-group {
  display: flex;
  align-items: center;
  gap: 6px;
}

.action-btn {
  padding: 4px 10px;
  border-radius: var(--radius-sm, 6px);
  border: 1px solid var(--border, rgba(0, 0, 0, 0.1));
  background: #ffffff;
  font-size: 11.5px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
  color: var(--text);
}

.action-btn:hover:not(:disabled) {
  background: #f9fafb;
}

.action-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.action-btn.accent {
  background: var(--accent, #28995a);
  color: #ffffff;
  border-color: var(--accent, #28995a);
}

.action-btn.accent:hover:not(:disabled) {
  background: #22834d;
}

.action-btn.danger-bg {
  background: #dc2626;
  color: #ffffff;
  border-color: #dc2626;
}

.action-btn.danger-bg:hover:not(:disabled) {
  background: #b91c1c;
}

.action-btn.danger {
  border-color: #fecaca;
  color: #dc2626;
  background: #ffffff;
}

.action-btn.danger:hover:not(:disabled) {
  background: #fef2f2;
}

.action-btn.danger-text {
  color: #dc2626;
  border-color: var(--border, rgba(0, 0, 0, 0.1));
}

.stream-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.stream-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 4px;
  font-size: 12px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.04);
}

.section-header-left {
  display: flex;
  align-items: center;
  gap: 6px;
}

.section-title {
  font-weight: 700;
  color: var(--text);
  font-size: 12.5px;
}

.section-sub {
  font-weight: 400;
  color: var(--text-secondary);
  font-size: 11px;
}

.section-sub.oversized {
  color: #92400e;
}

.section-sub.failed {
  color: #b91c1c;
}

.section-header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.section-count-badge {
  font-size: 11px;
  color: var(--text-secondary);
  font-weight: 500;
}

.section-count-badge.uploading {
  color: #28995a;
  font-weight: 600;
}

.section-count-badge.oversized {
  color: #92400e;
  font-weight: 600;
}

.section-count-badge.failed {
  color: #b91c1c;
  font-weight: 600;
}

.section-count-badge.queue {
  color: #6b7280;
}

.mini-action-btn {
  padding: 2px 7px;
  border-radius: var(--radius-sm, 6px);
  border: 1px solid var(--border, rgba(0, 0, 0, 0.1));
  background: #ffffff;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.12s ease;
}

.mini-action-btn:hover:not(:disabled) {
  background: #f3f4f6;
}

.mini-action-btn.accent {
  background: var(--accent, #28995a);
  color: #ffffff;
  border-color: var(--accent, #28995a);
}

.mini-action-btn.danger {
  border-color: #fca5a5;
  color: #dc2626;
  background: #ffffff;
}

.mini-action-btn.danger:hover:not(:disabled) {
  background: #fef2f2;
}

.section-cards {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.empty-state-card {
  padding: 18px;
  border-radius: var(--radius-md, 10px);
  border: 1px dashed var(--border, rgba(0, 0, 0, 0.1));
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-secondary);
  background: #fafbfa;
}

.empty-dot {
  width: 5px;
  height: 5px;
  border-radius: var(--radius-full, 9999px);
  background: #9ca3af;
}

.empty-board-wrap {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px 16px;
}

.empty-board-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}

.empty-icon-ring {
  width: 44px;
  height: 44px;
  border-radius: var(--radius-full, 9999px);
  background: #f3f5f3;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 12px;
}

.empty-svg {
  width: 22px;
  height: 22px;
  color: var(--accent, #28995a);
}

.empty-title {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text);
  margin: 0 0 4px;
}

.empty-desc {
  font-size: 11.5px;
  color: var(--text-secondary);
  margin: 0;
}

.board {
  display: flex;
  gap: 12px;
  min-width: 0;
  min-height: 0;
  height: 100%;
  align-items: stretch;
}

.rail {
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  align-self: stretch;
  gap: 8px;
  width: 48px;
  min-height: 0;
  overflow-y: auto;
}

.rail-tab {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  min-height: 96px;
  padding: 12px 6px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md, 10px);
  background: var(--col-pending);
  color: var(--text);
  cursor: pointer;
  transition:
    border-color 0.2s cubic-bezier(0.32, 0.72, 0, 1),
    transform 0.15s cubic-bezier(0.32, 0.72, 0, 1);
}

.rail-tab:hover {
  border-color: rgba(0, 0, 0, 0.12);
}

.rail-tab:active {
  transform: scale(0.98);
}

.rail-tab.preparing {
  background: var(--col-preparing);
}

.rail-tab.pending {
  background: var(--col-pending);
}

.rail-tab.uploading {
  background: var(--col-uploading);
}

.rail-tab.oversized {
  background: var(--col-oversized);
}

.rail-tab.failed {
  background: var(--col-failed);
}

.rail-title {
  writing-mode: vertical-rl;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.14em;
}

.open-pane {
  position: relative;
  display: flex;
  flex: 1;
  gap: 12px;
  min-width: 0;
  min-height: 0;
  height: 100%;
}

.well {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 220px;
  min-height: 0;
  height: 100%;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md, 10px);
  background: var(--col-pending);
}

.well.preparing {
  background: var(--col-preparing);
}

.well.pending {
  background: var(--col-pending);
}

.well.uploading {
  background: var(--col-uploading);
}

.well.oversized {
  background: var(--col-oversized);
}

.well.failed {
  background: var(--col-failed);
}

.well-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
  gap: 8px;
  margin-bottom: 10px;
  padding: 0 2px;
}

.well-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}

.batch-wrap {
  position: relative;
}

.batch-menu {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  z-index: 5;
  width: 168px;
  padding: 6px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md, 10px);
  box-shadow: 0 12px 32px rgba(24, 36, 25, 0.12);
}

.batch-menu button {
  display: block;
  width: 100%;
  text-align: left;
  border: 0;
  background: transparent;
  border-radius: var(--radius-sm, 6px);
  padding: 7px 8px;
  font-size: 13px;
  color: var(--text);
  cursor: pointer;
}

.batch-menu button:hover:not(:disabled) {
  background: var(--hover);
}

.batch-menu button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.batch-menu button.danger {
  color: #dc2626;
}

.batch-menu hr {
  border: 0;
  border-top: 1px solid var(--border-light);
  margin: 4px 0;
}

.well-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.well-count {
  min-width: 20px;
  padding: 1px 7px;
  border-radius: var(--radius-full, 9999px);
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 11px;
  font-weight: 500;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.well.oversized .well-count,
.rail-tab.oversized .well-count {
  background: #f8efe0;
  color: var(--warn);
}

.well.failed .well-count,
.rail-tab.failed .well-count {
  background: #fdecee;
  color: var(--bad);
}

.retry-all-btn {
  padding: 2px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm, 6px);
  background: var(--surface);
  color: var(--text);
  font-size: 11.5px;
  font-weight: 500;
  cursor: pointer;
  transition:
    background-color 0.15s cubic-bezier(0.32, 0.72, 0, 1),
    border-color 0.15s cubic-bezier(0.32, 0.72, 0, 1),
    transform 0.15s cubic-bezier(0.32, 0.72, 0, 1);
}

.retry-all-btn:hover:not(:disabled) {
  border-color: rgba(0, 0, 0, 0.12);
  background: var(--hover);
}

.retry-all-btn:active:not(:disabled) {
  transform: scale(0.97);
}

.retry-all-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.retry-all-btn.danger {
  color: var(--bad);
}

.well.failed .well-head,
.well.oversized .well-head {
  flex-wrap: wrap;
  row-gap: 8px;
}

.well.failed .well-actions,
.well.oversized .well-actions {
  flex-wrap: wrap;
  justify-content: flex-end;
}

.collapse-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: none;
  border-radius: var(--radius-sm, 6px);
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition:
    background-color 0.15s cubic-bezier(0.32, 0.72, 0, 1),
    color 0.15s cubic-bezier(0.32, 0.72, 0, 1),
    transform 0.15s cubic-bezier(0.32, 0.72, 0, 1);
}

.collapse-btn:hover:not(:disabled) {
  background: rgba(0, 0, 0, 0.05);
  color: var(--text);
}

.collapse-btn:active:not(:disabled) {
  transform: scale(0.96);
}

.collapse-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.collapse-btn:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.rail-tab:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.well-body {
  position: relative;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  min-height: 0;
  flex: 1;
}

.card-stack {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.empty {
  margin: 28px 0 8px;
  text-align: center;
  font-size: 12px;
  color: var(--text-secondary);
}

.kanban-move,
.pane-move {
  transition: transform 280ms cubic-bezier(0.32, 0.72, 0, 1);
}

.kanban-enter-active,
.pane-enter-active {
  transition:
    opacity 280ms cubic-bezier(0.32, 0.72, 0, 1),
    transform 280ms cubic-bezier(0.32, 0.72, 0, 1);
}

.kanban-leave-active,
.pane-leave-active {
  position: absolute;
  width: calc(100% - 0px);
  transition: opacity 80ms cubic-bezier(0.32, 0.72, 0, 1);
}

.kanban-enter-from,
.pane-enter-from {
  opacity: 0;
  transform: translateY(8px);
}

.kanban-leave-to,
.pane-leave-to {
  opacity: 0;
}

.rail-enter-active,
.rail-leave-active {
  transition: opacity 280ms cubic-bezier(0.32, 0.72, 0, 1);
}

.rail-enter-from,
.rail-leave-to {
  opacity: 0;
}

@media (prefers-reduced-motion: reduce) {
  .kanban-move,
  .kanban-enter-active,
  .kanban-leave-active,
  .pane-move,
  .pane-enter-active,
  .pane-leave-active,
  .rail-enter-active,
  .rail-leave-active,
  .rail-tab,
  .collapse-btn {
    transition: none;
  }
}

/* ── 移动端顶部分段状态栏 ── */
.mobile-board-tabs {
  display: none;
}

@media (max-width: 768px) {
  .board {
    display: flex;
    flex-direction: column;
    overflow: visible;
    height: auto;
    gap: 8px;
  }

  .mobile-board-tabs {
    display: flex;
    gap: 6px;
    overflow-x: auto;
    scrollbar-width: none;
    -webkit-overflow-scrolling: touch;
    padding: 2px 2px 4px;
    flex-shrink: 0;
  }

  .mobile-board-tabs::-webkit-scrollbar {
    display: none;
  }

  .mobile-tab-pill {
    flex: 1;
    min-width: fit-content;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 6px 12px;
    border-radius: var(--radius-full, 9999px);
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text-secondary);
    font-size: 12.5px;
    font-weight: 500;
    cursor: pointer;
    white-space: nowrap;
    transition: all 0.15s ease;
  }

  .mobile-tab-pill.active {
    background: var(--surface);
    color: var(--text);
    border-color: var(--accent);
    font-weight: 600;
    box-shadow: 0 1px 4px rgba(40, 153, 90, 0.15);
  }

  .tab-count {
    padding: 1px 6px;
    border-radius: var(--radius-full, 9999px);
    background: var(--hover);
    color: var(--text-secondary);
    font-size: 11px;
    font-weight: 500;
  }

  .tab-count.has-items {
    background: var(--accent-soft);
    color: var(--accent);
    font-weight: 600;
  }

  .mobile-tab-pill.failed.active {
    border-color: var(--bad);
    box-shadow: 0 1px 4px rgba(226, 77, 93, 0.15);
  }

  .mobile-tab-pill.failed .tab-count.has-items {
    background: #fdecee;
    color: var(--bad);
  }

  .mobile-tab-pill.oversized.active {
    border-color: var(--warn);
    box-shadow: 0 1px 4px rgba(217, 119, 6, 0.15);
  }

  .mobile-tab-pill.oversized .tab-count.has-items {
    background: #f8efe0;
    color: var(--warn);
  }

  .open-pane {
    display: flex;
    flex-direction: column;
    overflow: visible;
    height: auto;
    flex: 1;
    min-height: 0;
  }

  .well {
    width: 100%;
    flex: 1;
    min-width: 0;
    height: auto;
    min-height: 280px;
    padding: 12px 14px;
    border-radius: var(--radius-md, 10px);
  }

  .well-body {
    overflow: visible;
    height: auto;
  }

  .well.failed .well-head,
  .well.oversized .well-head {
    row-gap: 6px;
  }

  .well.failed .well-actions,
  .well.oversized .well-actions {
    gap: 4px;
  }

  .retry-all-btn {
    padding: 4px 8px;
    font-size: 11px;
  }
}
</style>

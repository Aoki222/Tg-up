<script setup lang="ts">
/**
 * 单栏任务流：全部 / 正在上传 / 过大 / 失败 / 排队。
 * 排队包含封面生成和等待上传。过大、失败才出现批量菜单。
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
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

type BatchKey = "oversized" | "failed";

const props = defineProps<{ items: BoardTask[] }>();

const retryingId = ref<number | null>(null);
const dispatchingId = ref<number | null>(null);
const slicingId = ref<number | null>(null);
const retryingAll = ref(false);
const clearing = ref(false);
const oversizedActing = ref(false);
const selectedIds = ref<Set<number>>(new Set());
const oversizedIds = ref<Set<number>>(new Set());
const batchOpen = ref<BatchKey | null>(null);
type BoardTab = "all" | "uploading" | "oversized" | "failed" | "queue";
const boardTab = ref<BoardTab>("all");

function toggleStreamBatch(): void {
  const key = boardTab.value;
  if (key !== "oversized" && key !== "failed") return;
  batchOpen.value = batchOpen.value === key ? null : key;
}

function closeBatch(): void {
  batchOpen.value = null;
}

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
const streamSections = computed(() => {
  const all = [
    { key: "uploading" as const, title: "正在上传", items: buckets.value.uploading },
    { key: "oversized" as const, title: "过大", items: buckets.value.oversized },
    { key: "failed" as const, title: "失败", items: buckets.value.failed },
    { key: "queue" as const, title: "排队", items: queueItems.value },
  ];
  if (boardTab.value === "all") return all.filter((section) => section.items.length > 0);
  return all.filter((section) => section.key === boardTab.value);
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

onMounted(() => {
  const onPointer = (event: Event) => {
    const target = event.target;
    if (target instanceof Element && target.closest(".batch-wrap")) return;
    batchOpen.value = null;
  };
  document.addEventListener("pointerdown", onPointer);
  onUnmounted(() => {
    document.removeEventListener("pointerdown", onPointer);
  });
});
</script>

<template>
  <section class="stream" aria-label="任务看板">
    <div class="stream-tabs">
      <button type="button" :class="{ on: boardTab === 'all' }" @click="boardTab = 'all'">
        全部 <span>{{ activeTotal }}</span>
      </button>
      <button type="button" :class="{ on: boardTab === 'uploading' }" @click="boardTab = 'uploading'">
        正在上传 <span>{{ buckets.uploading.length }}</span>
      </button>
      <button type="button" :class="{ on: boardTab === 'oversized' }" @click="boardTab = 'oversized'">
        过大 <span>{{ buckets.oversized.length }}</span>
      </button>
      <button type="button" :class="{ on: boardTab === 'failed' }" @click="boardTab = 'failed'">
        失败 <span>{{ buckets.failed.length }}</span>
      </button>
      <button type="button" :class="{ on: boardTab === 'queue' }" @click="boardTab = 'queue'">
        排队 <span>{{ queueItems.length }}</span>
      </button>
      <div v-if="boardTab === 'oversized' || boardTab === 'failed'" class="batch-wrap stream-batch">
        <button type="button" class="retry-all-btn" :disabled="failedBusy" @click.stop="toggleStreamBatch">批量</button>
        <div v-if="batchOpen === boardTab" class="batch-menu" @click.stop>
          <template v-if="boardTab === 'oversized'">
            <button type="button" :disabled="failedBusy || oversizedCount === 0" @click="onSliceBatch([...oversizedIds])">切片选中</button>
            <button type="button" :disabled="failedBusy || buckets.oversized.length === 0" @click="onSliceBatch([])">全部切片</button>
            <button type="button" :disabled="failedBusy || oversizedCount === 0" @click="onDispatchBatch([...oversizedIds])">交给个人号</button>
            <button type="button" :disabled="failedBusy || buckets.oversized.length === 0" @click="onDispatchBatch([])">全部交给个人号</button>
            <hr />
            <button type="button" :disabled="failedBusy || oversizedCount === 0" @click="onRemoveOversizedSelected">清除选中</button>
            <button type="button" class="danger" :disabled="failedBusy || buckets.oversized.length === 0" @click="onRemoveOversizedAll">全部清除</button>
          </template>
          <template v-else>
            <button type="button" :disabled="failedBusy || buckets.failed.length === 0" @click="onRetryAll">{{ retryingAll ? "重试中" : "全部重试" }}</button>
            <button type="button" :disabled="failedBusy || selectedCount === 0" @click="onRemoveSelected">清除选中</button>
            <button type="button" class="danger" :disabled="failedBusy || buckets.failed.length === 0" @click="onRemoveAll">全部清除</button>
          </template>
        </div>
      </div>
    </div>
    <div class="stream-scroll">
      <section v-for="section in streamSections" :key="section.key" class="stream-section">
        <h3 v-if="boardTab === 'all'">{{ section.title }} <span>{{ section.items.length }}</span></h3>
        <p v-if="section.items.length === 0" class="empty">这一栏是空的</p>
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
      </section>
      <p v-if="streamSections.length === 0" class="empty">没有进行中的任务</p>
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
  border-radius: 12px;
  box-shadow: var(--shadow);
  overflow: hidden;
}

.stream-tabs {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 4px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border-light);
  flex-shrink: 0;
  overflow-x: auto;
}

.stream-tabs > button {
  flex-shrink: 0;
  border: 0;
  background: transparent;
  border-radius: 8px;
  padding: 6px 10px;
  font-size: 12px;
  color: var(--text-secondary);
  cursor: pointer;
}

.stream-tabs > button.on {
  background: #fff;
  color: var(--text);
  font-weight: 700;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
}

.stream-tabs > button span {
  margin-left: 4px;
  font-variant-numeric: tabular-nums;
}

.stream-batch {
  margin-left: auto;
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

.stream-section h3 {
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.stream-section h3 span {
  margin-left: 6px;
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
  border-radius: 16px;
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
  border-radius: 16px;
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
  border-radius: 12px;
  box-shadow: 0 12px 32px rgba(24, 36, 25, 0.12);
}

.batch-menu button {
  display: block;
  width: 100%;
  text-align: left;
  border: 0;
  background: transparent;
  border-radius: 8px;
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
  border-radius: 9999px;
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
  border-radius: 8px;
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
  border-radius: 8px;
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
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 6px 12px;
    border-radius: 9999px;
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
    border-radius: 9999px;
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
    border-radius: 14px;
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

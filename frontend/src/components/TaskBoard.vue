<script setup lang="ts">
/**
 * 四列观察看板：封面 / 等待 / 上传中 / 失败。
 * 列可收起：收起的列进左侧 48px 轨，展开列均分剩余宽度。
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import type { BoardTask } from "../types";
import TaskCard from "./TaskCard.vue";

const STORAGE_KEY = "uploader.kanban.collapsed";

const columns = [
  { key: "preparing", title: "封面", empty: "没有封面任务" },
  { key: "pending", title: "等待", empty: "队列空闲" },
  { key: "uploading", title: "上传中", empty: "没有在传" },
  { key: "failed", title: "失败", empty: "没有失败" },
] as const;

type ColumnKey = (typeof columns)[number]["key"];

const COLUMN_KEYS: ColumnKey[] = columns.map((column) => column.key);

const props = defineProps<{ items: BoardTask[] }>();

const isNarrow = ref(false);
const collapsed = ref<Set<ColumnKey>>(loadCollapsed());

const buckets = computed(() => {
  const preparing: BoardTask[] = [];
  const pending: BoardTask[] = [];
  const uploading: BoardTask[] = [];
  const failed: BoardTask[] = [];
  for (const item of props.items) {
    if (item.status === "preparing") preparing.push(item);
    else if (item.status === "pending") pending.push(item);
    else if (item.status === "assigned" || item.status === "uploading") uploading.push(item);
    else if (item.status === "failed") failed.push(item);
  }
  return { preparing, pending, uploading, failed };
});

const rail = computed(() => columns.filter((column) => collapsed.value.has(column.key)));
const open = computed(() => columns.filter((column) => !collapsed.value.has(column.key)));
const visibleWells = computed(() => (isNarrow.value ? [...columns] : open.value));
const canCollapse = computed(() => open.value.length > 1);

function loadCollapsed(): Set<ColumnKey> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return new Set();
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set();
    const keys = parsed.filter((key): key is ColumnKey => COLUMN_KEYS.includes(key as ColumnKey));
    const next = new Set(keys);
    if (next.size >= COLUMN_KEYS.length) {
      next.delete("uploading");
    }
    return next;
  } catch {
    return new Set();
  }
}

function persist(): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify([...collapsed.value]));
}

function collapse(key: ColumnKey): void {
  if (open.value.length <= 1 || collapsed.value.has(key)) return;
  collapsed.value = new Set([...collapsed.value, key]);
  persist();
}

function expand(key: ColumnKey): void {
  if (!collapsed.value.has(key)) return;
  const next = new Set(collapsed.value);
  next.delete(key);
  collapsed.value = next;
  persist();
}

onMounted(() => {
  const media = window.matchMedia("(max-width: 768px)");
  const apply = () => {
    isNarrow.value = media.matches;
  };
  apply();
  media.addEventListener("change", apply);
  onUnmounted(() => {
    media.removeEventListener("change", apply);
  });
});
</script>

<template>
  <section class="board" :class="{ 'has-rail': !isNarrow && rail.length > 0 }" aria-label="任务看板">
    <Transition name="rail">
      <aside v-if="!isNarrow && rail.length" class="rail" aria-label="已收起的列">
        <button
          v-for="column in rail"
          :key="column.key"
          type="button"
          class="rail-tab"
          :class="column.key"
          :aria-label="`展开${column.title}`"
          @click="expand(column.key)"
        >
          <span class="rail-title">{{ column.title }}</span>
          <span class="well-count">{{ buckets[column.key].length }}</span>
        </button>
      </aside>
    </Transition>

    <TransitionGroup name="pane" tag="div" class="open-pane">
      <div v-for="column in visibleWells" :key="column.key" class="well" :class="column.key">
        <header class="well-head">
          <span class="well-title">{{ column.title }}</span>
          <div class="well-actions">
            <span class="well-count">{{ buckets[column.key].length }}</span>
            <button
              v-if="!isNarrow"
              type="button"
              class="collapse-btn"
              :disabled="!canCollapse"
              :aria-label="`收起${column.title}`"
              @click="collapse(column.key)"
            >
              <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
                <path
                  d="M10 3L5 8l5 5"
                  stroke="currentColor"
                  stroke-width="1.6"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
              </svg>
            </button>
          </div>
        </header>
        <div class="well-body">
          <TransitionGroup name="kanban" tag="div" class="card-stack">
            <TaskCard v-for="task in buckets[column.key]" :key="task.id" :task="task" />
          </TransitionGroup>
          <p v-if="buckets[column.key].length === 0" class="empty">{{ column.empty }}</p>
        </div>
      </div>
    </TransitionGroup>
  </section>
</template>

<style scoped>
.board {
  display: flex;
  gap: 12px;
  min-width: 0;
  align-items: stretch;
}

.rail {
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  gap: 8px;
  width: 48px;
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
}

.well {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 220px;
  min-height: 280px;
  max-height: calc(100dvh - 280px);
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

.well.failed {
  background: var(--col-failed);
}

.well-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
  padding: 0 2px;
}

.well-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
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

.well.failed .well-count,
.rail-tab.failed .well-count {
  background: #fdecee;
  color: var(--bad);
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

@media (max-width: 768px) {
  .board {
    display: flex;
    overflow-x: auto;
    scroll-snap-type: x mandatory;
    padding-bottom: 8px;
  }

  .open-pane {
    display: flex;
    overflow: visible;
  }

  .well {
    flex: 0 0 calc(100vw - 32px);
    max-height: none;
    scroll-snap-align: start;
  }
}
</style>

<script setup lang="ts">
/**
 * 工作台：顶栏固定，左侧监听目录，右侧单栏任务流。设置和成功记录都在本页弹层里。
 */

import { computed, nextTick, onDeactivated, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import WorkerPanel from "../components/WorkerPanel.vue";
import TaskBoard from "../components/TaskBoard.vue";
import SessionPanel from "../components/SessionPanel.vue";
import SettingsPage from "./SettingsPage.vue";
import { fetchSettings, fetchSuccessPage, fetchSystemVersion } from "../api";
import type { SuccessRecord } from "../api";
import { useTaskBoard } from "../composables/useTaskBoard";
import { useTelegramIdentity } from "../composables/useTelegramIdentity";
import { useUnmatchedPolling } from "../composables/useUnmatched";
import { formatBytes, formatSpeed } from "../format";
import type { FolderRouteItem, ObserverPathInfo, SystemVersionInfo, WorkerSnapshot } from "../types";

defineOptions({ name: "MonitorPage" });

// ── 响应式状态定义 ─────────────────────────────────────────────

/** 控制添加 Session 模态弹窗的显示与隐藏 */
const showSessionForm = ref(false);
const route = useRoute();
const router = useRouter();
const { configured, refresh: refreshIdentity } = useTelegramIdentity();
const dockMedia = window.matchMedia("(max-width: 860px)");
const dockOpen = ref(!dockMedia.matches);
const settingsOpen = ref(false);
const settingsDirty = ref(false);
const watchPaths = ref<string[]>([]);
const pathInfos = ref<ObserverPathInfo[]>([]);
const routes = ref<FolderRouteItem[]>([]);
const settingsRef = ref<{ openRouteSettings: (path: string) => void; setSection: (id: string) => void } | null>(null);
const versionInfo = ref<SystemVersionInfo | null>(null);

/** 由子组件 WorkerPanel 派发的最新 Worker 快照数组 */
const workers = ref<WorkerSnapshot[]>([]);

const { items: boardItems, successToday } = useTaskBoard();

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

function applyDock(): void {
  dockOpen.value = !dockMedia.matches;
}

dockMedia.addEventListener("change", applyDock);
onUnmounted(() => dockMedia.removeEventListener("change", applyDock));

onMounted(() => {
  void refreshIdentity();
  void loadWatchPaths();
  void fetchSystemVersion().then((info) => {
    versionInfo.value = info;
  }).catch(() => undefined);
});

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

const liveSpeed = computed(() => {
  const current = boardItems.value.find((item) => item.status === "uploading" || item.status === "assigned");
  if (!current) return "—";
  if (current.speed_bps > 0) return formatSpeed(current.speed_bps);
  return "测算中";
});

const versionLabel = computed(() => {
  const info = versionInfo.value;
  if (!info) return "";
  const rev = shortRevision(info.current_version);
  if (info.status === "update") return "有更新";
  if (info.status === "dev") return "开发";
  if (info.status === "staging") return rev ? `测试版 ${rev}` : "测试版";
  return rev ? `正式版 ${rev}` : "正式版";
});

const versionTitle = computed(() => versionInfo.value?.commit_message || "");

const dockItems = computed(() =>
  watchPaths.value.map((path) => {
    const info = pathInfos.value.find((item) => samePath(item.path, path));
    return {
      path,
      name: pathName(path),
      ok: info ? info.ok : true,
      error: info?.error || "",
      children: childRoutes(path),
    };
  }),
);

function shortRevision(value: string): string {
  const text = value.trim();
  if (!text || text === "dev" || text === "local_dev") return "";
  return text.length > 7 ? text.slice(0, 7) : text;
}

function normPath(path: string): string {
  return path.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

function samePath(left: string, right: string): boolean {
  return normPath(left) === normPath(right);
}

function childRoutes(path: string): { path: string; label: string }[] {
  const key = normPath(path);
  return routes.value
    .filter((item) => {
      if (!item.enabled) return false;
      const child = normPath(item.path);
      if (child === key || !child.startsWith(`${key}/`)) return false;
      const owner = longestWatch(item.path);
      return owner != null && samePath(owner, path);
    })
    .map((item) => ({ path: item.path, label: item.name || pathName(item.path) }));
}

function longestWatch(path: string): string | null {
  const key = normPath(path);
  let best: string | null = null;
  let bestLen = -1;
  for (const watch of watchPaths.value) {
    const root = normPath(watch);
    if ((key === root || key.startsWith(`${root}/`)) && root.length > bestLen) {
      best = watch;
      bestLen = root.length;
    }
  }
  return best;
}

async function loadWatchPaths(): Promise<void> {
  try {
    const settings = await fetchSettings();
    watchPaths.value = settings.observer_paths;
    pathInfos.value = settings.observer_path_infos;
    routes.value = settings.routes;
  } catch {
    watchPaths.value = [];
    pathInfos.value = [];
    routes.value = [];
  }
}

function openSettings(tab = "telegram"): void {
  if (String(route.query.settings || "") === tab) {
    settingsOpen.value = true;
    void nextTick(() => settingsRef.value?.setSection(tab));
    return;
  }
  void router.replace({ query: { ...route.query, settings: tab } });
}

function closeSettings(): void {
  if (!route.query.settings) {
    settingsOpen.value = false;
    void loadWatchPaths();
    return;
  }
  const next = { ...route.query };
  delete next.settings;
  void router.replace({ query: next });
}

function onSettingsSection(id: string): void {
  if (String(route.query.settings || "") === id) return;
  void router.replace({ query: { ...route.query, settings: id } });
}

function openPolicy(path: string): void {
  settingsRef.value?.openRouteSettings(path);
}

function pathName(path: string): string {
  const parts = path.replace(/\\/g, "/").split("/").filter(Boolean);
  return parts[parts.length - 1] || path;
}

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
watch([showSessionForm, successOpen, settingsOpen], ([sessionOpen, historyOpen, settings]) => {
  document.body.style.overflow = sessionOpen || historyOpen || settings ? "hidden" : "";
});

watch(successCount, () => {
  if (successOpen.value && successPage.value === 1) void loadSuccess();
});

watch(
  () => String(route.query.settings || ""),
  (requested) => {
    if (!requested) {
      const wasOpen = settingsOpen.value;
      settingsOpen.value = false;
      if (wasOpen) void loadWatchPaths();
      return;
    }
    settingsOpen.value = true;
    void nextTick(() => settingsRef.value?.setSection(requested));
  },
  { immediate: true },
);
</script>

<template>
  <div class="workbench">
    <header class="work-header">
      <div class="header-lead">
        <button
          type="button"
          class="text-btn"
          :aria-expanded="dockOpen"
          @click="dockOpen = !dockOpen"
        >
          {{ dockOpen ? "收起目录" : "目录" }}
        </button>
        <strong class="brand">Telegram Uploader</strong>
        <span
          v-if="versionLabel"
          class="version-pill"
          :class="versionInfo ? `tag-${versionInfo.status}` : ''"
          :title="versionTitle"
        >{{ versionLabel }}</span>
      </div>
      <div class="header-stats">
        <span>速度 <b>{{ liveSpeed }}</b></span>
        <span class="stat-gap"></span>
        <span>Worker <b>{{ activeWorkersCount }} / {{ totalWorkersCount }}</b></span>
        <span class="stat-gap"></span>
        <button type="button" class="today-btn" @click="openSuccess">
          今日上传 <b>{{ successCount }}</b>
        </button>
      </div>
      <button type="button" class="text-btn settings-btn" @click="openSettings('telegram')">
        设置
        <span v-if="settingsDirty" class="dirty-dot" aria-label="有未保存的修改"></span>
      </button>
    </header>

    <p v-if="configured === false" class="setup-banner">
      请先配置 API_ID / API_HASH，保存后当前进程即可使用。
      <button type="button" @click="openSettings('account')">去配置</button>
    </p>
    <p v-if="unmatchedCount > 0" class="unmatched-banner">
      {{ unmatchedCount }} 个文件未命中路由，未进入上传队列。在左侧目录的「策略」里指定群。
    </p>

    <div class="work-body">
      <button
        v-if="dockOpen"
        type="button"
        class="dock-scrim"
        aria-label="关闭目录"
        @click="dockOpen = false"
      ></button>
      <aside class="dock" :class="{ shut: !dockOpen }">
        <div class="dock-head">
          <h2>监控目录</h2>
          <span>{{ watchPaths.length }}</span>
        </div>
        <div class="dock-list">
          <p v-if="dockItems.length === 0" class="dock-empty">还没有监听目录</p>
          <article v-for="item in dockItems" :key="item.path" class="dock-item">
            <div class="dock-row">
              <span class="dock-dot" :class="{ bad: !item.ok }" :title="item.error || '可读取'"></span>
              <strong :title="item.path">{{ item.name }}</strong>
              <button type="button" @click="openPolicy(item.path)">策略</button>
            </div>
            <p class="dock-path" :title="item.path">{{ item.path }}</p>
            <div v-if="item.children.length" class="dock-children">
              <button
                v-for="child in item.children"
                :key="child.path"
                type="button"
                :title="child.path"
                @click="openPolicy(child.path)"
              >
                {{ child.label }}
              </button>
            </div>
          </article>
        </div>
        <div class="dock-workers">
          <WorkerPanel @add="openSessionForm" @update-workers="onUpdateWorkers" />
        </div>
        <button type="button" class="add-path" @click="openSettings('watch')">添加监控路径</button>
      </aside>
      <TaskBoard class="board-pane" :items="boardItems" />
    </div>

    <div v-show="settingsOpen" class="settings-layer" @click.self="closeSettings">
      <div class="settings-modal" role="dialog" aria-label="设置" @click.stop>
        <header class="settings-head">
          <h2>设置</h2>
          <span v-if="settingsDirty" class="unsaved">未保存</span>
          <button type="button" @click="closeSettings">关闭</button>
        </header>
        <SettingsPage
          ref="settingsRef"
          embedded
          @update:dirty="settingsDirty = $event"
          @section="onSettingsSection"
        />
      </div>
    </div>

    <Teleport to="body">
      <div v-if="showSessionForm" class="session-overlay" @click.self="closeSessionForm">
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
              <button type="button" :class="{ on: successScope === 'today' }" @click="onSuccessScopeChange('today')">今日</button>
              <button type="button" :class="{ on: successScope === 'all' }" @click="onSuccessScopeChange('all')">累计</button>
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
            <button type="button" :disabled="successPage <= 1 || successLoading" @click="shiftSuccessPage(-1)">上一页</button>
            <span>第 {{ successPage }} / {{ successPages }} 页</span>
            <button type="button" :disabled="successPage >= successPages || successLoading" @click="shiftSuccessPage(1)">下一页</button>
          </footer>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.workbench {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  height: 100%;
  overflow: hidden;
  background: var(--surface-subtle, #f6f7f5);
}

.work-header {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
  min-height: 52px;
  padding: 8px 14px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
}

.header-lead,
.header-stats {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.header-stats {
  margin-left: auto;
  color: var(--text-secondary);
  font-size: 12px;
  white-space: nowrap;
}

.header-stats b,
.today-btn b {
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.stat-gap {
  width: 1px;
  height: 12px;
  background: var(--border);
}

.brand {
  font-size: 14px;
  letter-spacing: -0.02em;
}

.text-btn,
.today-btn,
.add-path,
.dock-row button,
.dock-children button,
.settings-head button {
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  border-radius: 8px;
  cursor: pointer;
}

.text-btn,
.today-btn,
.settings-head button {
  padding: 5px 10px;
  font-size: 12px;
}

.today-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border-color: rgba(40, 153, 90, 0.28);
  background: rgba(40, 153, 90, 0.06);
}

.settings-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.dirty-dot,
.unsaved {
  color: var(--warn, #b45309);
}

.dirty-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: currentColor;
}

.version-pill {
  padding: 2px 7px;
  border-radius: 999px;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  background: rgba(0, 0, 0, 0.04);
  color: var(--text-secondary);
}

.version-pill.tag-latest {
  color: #047857;
  background: rgba(16, 185, 129, 0.1);
}

.version-pill.tag-staging {
  color: #b45309;
  background: rgba(245, 158, 11, 0.12);
}

.version-pill.tag-update {
  color: #b91c1c;
  background: rgba(239, 68, 68, 0.1);
}

.setup-banner,
.unmatched-banner {
  margin: 8px 14px 0;
  padding: 8px 12px;
  border-radius: 10px;
  font-size: 13px;
  flex-shrink: 0;
}

.setup-banner {
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text-secondary);
}

.setup-banner button {
  margin-left: 8px;
  border: 0;
  background: transparent;
  color: var(--accent);
  font-weight: 650;
  cursor: pointer;
}

.unmatched-banner {
  border: 1px solid #f3d7a1;
  background: #fdf5ea;
  color: var(--warn);
}

.work-body {
  position: relative;
  display: flex;
  flex: 1;
  min-height: 0;
  gap: 12px;
  padding: 12px;
}

.dock {
  width: 280px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 10px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: var(--shadow);
}

.dock.shut {
  display: none;
}

.dock-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border-light);
}

.dock-head h2 {
  margin: 0;
  font-size: 12px;
  font-weight: 700;
}

.dock-head span,
.dock-empty {
  color: var(--text-secondary);
  font-size: 12px;
}

.dock-list {
  flex: 1;
  min-height: 0;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px 0;
}

.dock-item {
  padding: 8px;
  border: 1px solid var(--border-light);
  border-radius: 10px;
  background: #f7f8f6;
}

.dock-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.dock-row strong {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}

.dock-row button,
.dock-children button {
  padding: 1px 7px;
  font-size: 11px;
}

.dock-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent);
  flex-shrink: 0;
}

.dock-dot.bad {
  background: var(--warn, #d97706);
}

.dock-path {
  margin: 3px 0 0;
  color: var(--text-secondary);
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dock-children {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 6px;
}

.dock-workers {
  max-height: 42%;
  overflow: auto;
  border-top: 1px solid var(--border-light);
  margin-top: 4px;
}

.dock-workers :deep(.worker-card) {
  border: 0;
  box-shadow: none;
  background: transparent;
}

.dock-workers :deep(.el-card__header),
.dock-workers :deep(.el-card__body) {
  padding-left: 0;
  padding-right: 0;
}

.dock-workers :deep(.list) {
  flex-direction: column;
  overflow: visible;
}

.dock-workers :deep(.item) {
  min-width: 0;
}

.dock-workers :deep(.empty-state) {
  padding: 12px 4px;
}

.add-path {
  margin-top: 8px;
  padding: 7px 8px;
  border-style: dashed;
  font-size: 12px;
}

.board-pane {
  flex: 1;
  min-width: 0;
  min-height: 0;
}

.dock-scrim {
  display: none;
}

.settings-layer,
.session-overlay {
  position: fixed;
  inset: 0;
  z-index: 1800;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(22, 34, 25, 0.28);
}

.session-overlay {
  z-index: 2000;
}

.settings-modal {
  display: flex;
  flex-direction: column;
  width: min(1080px, 100%);
  height: min(86dvh, 860px);
  padding: 14px 16px 16px;
  background: var(--surface);
  border-radius: 16px;
  box-shadow: 0 24px 64px -12px rgba(18, 30, 20, 0.24);
}

.settings-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
  flex-shrink: 0;
}

.settings-head h2,
.success-head h2 {
  margin: 0;
  font-size: 15px;
}

.settings-head button,
.unsaved {
  margin-left: auto;
}

.unsaved {
  margin-left: 0;
  font-size: 12px;
}

.settings-modal :deep(.settings) {
  flex: 1;
  min-height: 0;
}

.session-modal {
  width: min(560px, 100%);
  max-height: min(90vh, 840px);
  overflow: auto;
  border-radius: 16px;
  background: var(--surface);
  box-shadow: 0 24px 64px -12px rgba(18, 30, 20, 0.24);
}

.success-modal {
  width: min(640px, 100%);
  padding: 16px 16px 12px;
}

.success-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
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

.success-part,
.success-meta,
.success-pager {
  color: var(--text-secondary);
  font-size: 12px;
}

.success-meta {
  margin-left: auto;
}

.success-pager {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 8px;
}

.success-pager button {
  border: 1px solid var(--border);
  background: var(--surface);
  border-radius: 8px;
  padding: 2px 8px;
  font-size: 12px;
  cursor: pointer;
}

.success-pager button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

@media (max-width: 860px) {
  .work-header {
    flex-wrap: wrap;
    align-items: flex-start;
  }

  .header-stats {
    order: 3;
    width: 100%;
    margin-left: 0;
    overflow-x: auto;
  }

  .dock-scrim {
    display: block;
    position: absolute;
    inset: 0;
    z-index: 15;
    border: 0;
    background: rgba(22, 34, 25, 0.18);
  }

  .dock:not(.shut) {
    position: absolute;
    z-index: 16;
    left: 12px;
    top: 12px;
    bottom: 12px;
    width: min(300px, calc(100% - 24px));
  }

  .settings-layer,
  .session-overlay {
    align-items: stretch;
    padding: 0;
  }

  .settings-modal,
  .session-modal {
    width: 100%;
    height: 100%;
    max-height: none;
    border-radius: 0;
  }
}
</style>

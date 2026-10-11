<script setup lang="ts">
/**
 * 工作台：顶栏固定，左侧监听目录，右侧多维任务调度看板。设置和传输历史记录均在本页模态弹层中。
 * 左右绝对等高、各自独立局部滑动，无整页滚动。
 */

import { computed, nextTick, onDeactivated, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import WorkerPanel from "../components/WorkerPanel.vue";
import TaskBoard from "../components/TaskBoard.vue";
import SessionPanel from "../components/SessionPanel.vue";
import DirectoryPolicyModal from "../components/DirectoryPolicyModal.vue";
import ManualDispatchModal from "../components/ManualDispatchModal.vue";
import SettingsPage from "./SettingsPage.vue";
import { useQueryClient } from "@tanstack/vue-query";
import {
  fetchFsNodes,
  fetchSettings,
  fetchSuccessPage,
  fetchSystemVersion,
  fetchWorkers,
  saveSettings,
  toggleGlobalWatch,
  toggleRootPath,
  toggleRoutePath,
} from "../api";
import type { SuccessRecord } from "../api";
import { useTaskBoard } from "../composables/useTaskBoard";
import { useTelegramIdentity } from "../composables/useTelegramIdentity";
import { useUnmatchedPolling } from "../composables/useUnmatched";
import { formatBytes, formatSpeed } from "../format";
import type {
  FolderRouteItem,
  FsNode,
  ManualDispatchResult,
  ObserverPathInfo,
  ObserverRootItem,
  SystemVersionInfo,
  WorkerSnapshot,
} from "../types";

defineOptions({ name: "MonitorPage" });

// ── 响应式状态定义 ─────────────────────────────────────────────

/** 控制添加 Session 模态弹窗的显示与隐藏 */
const showSessionForm = ref(false);
const manualDispatchOpen = ref(false);
const route = useRoute();
const router = useRouter();
const { configured, refresh: refreshIdentity } = useTelegramIdentity();
const dockMedia = window.matchMedia("(max-width: 860px)");
const dockOpen = ref(!dockMedia.matches);
const settingsOpen = ref(false);
const settingsDirty = ref(false);
const workerModalOpen = ref(false);
const policyModalOpen = ref(false);
const policyPath = ref("");
const watchPaths = ref<string[]>([]);
const pathInfos = ref<ObserverPathInfo[]>([]);
const routes = ref<FolderRouteItem[]>([]);
const watchEnabled = ref(true);
const observerRoots = ref<ObserverRootItem[]>([]);
const togglingGlobalWatch = ref(false);
const togglingRoots = ref<Set<string>>(new Set());
const togglingRoutes = ref<Set<string>>(new Set());
const settingsRef = ref<InstanceType<typeof SettingsPage> | null>(null);
const tomlSaving = computed(() => settingsRef.value?.saving ?? false);

// ── 方案一：卡片原地折叠文件树 (Inline Accordion Tree) ───────────────
const queryClient = useQueryClient();
const expandedPaths = ref<Set<string>>(new Set());
const treeRefsMap = new Map<string, any>();

function setTreeRef(path: string, el: any): void {
  if (el) {
    treeRefsMap.set(normPath(path), el);
  } else {
    treeRefsMap.delete(normPath(path));
  }
}

function isCardExpanded(path: string): boolean {
  return expandedPaths.value.has(normPath(path));
}

function toggleCardExpand(path: string): void {
  const norm = normPath(path);
  if (expandedPaths.value.has(norm)) {
    expandedPaths.value.delete(norm);
  } else {
    expandedPaths.value.add(norm);
  }
}

async function loadCardFsNodes(rootPath: string, node: any, resolve: (data: FsNode[]) => void): Promise<void> {
  const targetPath = node.level === 0 ? rootPath : (node.data as FsNode).path;
  try {
    const items = await queryClient.fetchQuery({
      queryKey: ["fs-nodes", targetPath],
      queryFn: () => fetchFsNodes(targetPath),
    });
    resolve(items);
  } catch {
    resolve([]);
  }
}

async function refreshCardRoot(rootPath: string): Promise<void> {
  await queryClient.invalidateQueries({ queryKey: ["fs-nodes", rootPath] });
  const treeEl = treeRefsMap.get(normPath(rootPath));
  if (treeEl?.store) {
    treeEl.store.root.loaded = false;
    treeEl.store.root.loadChildren();
  }
  ElMessage.success("目录结构已刷新");
}

async function refreshCardFsNode(node: any, data: FsNode): Promise<void> {
  await queryClient.invalidateQueries({ queryKey: ["fs-nodes", data.path] });
  if (node) {
    node.loaded = false;
    node.expand();
  }
}

async function restoreSubfolderInheritance(subPath: string): Promise<void> {
  try {
    const config = await fetchSettings();
    config.routes = (config.routes || []).filter((r) => !samePath(r.path, subPath));
    await saveSettings(config);
    await loadWatchPaths();
    await queryClient.invalidateQueries({ queryKey: ["fs-nodes"] });
    for (const [_, treeEl] of treeRefsMap) {
      if (treeEl?.store) {
        treeEl.store.root.loaded = false;
        treeEl.store.root.loadChildren();
      }
    }
    ElMessage.success("已清除专属规则，恢复跟随父级");
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : "恢复继承失败");
  }
}

async function onPolicySaved(): Promise<void> {
  await loadWatchPaths();
  await queryClient.invalidateQueries({ queryKey: ["fs-nodes"] });
  for (const [_, treeEl] of treeRefsMap) {
    if (treeEl?.store) {
      treeEl.store.root.loaded = false;
      treeEl.store.root.loadChildren();
    }
  }
}

async function onManualDispatched(_res: ManualDispatchResult): Promise<void> {
  await loadWatchPaths();
  await queryClient.invalidateQueries({ queryKey: ["fs-nodes"] });
  for (const [_, treeEl] of treeRefsMap) {
    if (treeEl?.store) {
      treeEl.store.root.loaded = false;
      treeEl.store.root.loadChildren();
    }
  }
}

async function handleSaveToml(): Promise<void> {
  await settingsRef.value?.submitToml();
  await loadWatchPaths();
  await queryClient.invalidateQueries({ queryKey: ["fs-nodes"] });
}
const versionInfo = ref<SystemVersionInfo | null>(null);

/** 由子组件 WorkerPanel 或直接轮询派发的最新 Worker 快照数组 */
const workers = ref<WorkerSnapshot[]>([]);
let workerTimer = 0;

async function refreshWorkers(): Promise<void> {
  try {
    workers.value = await fetchWorkers();
  } catch {
    // 忽略异常
  }
}

const { items: boardItems, successToday } = useTaskBoard();

const SUCCESS_SCOPE_KEY = "uploader.success-scope";
const { files: unmatchedFiles } = useUnmatchedPolling();
const unmatchedCount = computed(() => unmatchedFiles.value.length);

const successScope = ref<"today" | "all">(loadSuccessScope());
const successCount = computed(() => successToday.value);
const successAllTotal = ref(0);
const successOpen = ref(false);
const successPage = ref(1);
const successPageSize = 50;
const successTotalRows = ref(0);
const successItems = ref<SuccessRecord[]>([]);
const successLoading = ref(false);
const successSearchText = ref("");

const successPages = computed(() =>
  Math.max(1, Math.ceil(successTotalRows.value / successPageSize)),
);

const filteredSuccessItems = computed(() => {
  const query = successSearchText.value.trim().toLowerCase();
  if (!query) return successItems.value;
  return successItems.value.filter(
    (item) =>
      item.file_name.toLowerCase().includes(query) ||
      (item.folder_name && item.folder_name.toLowerCase().includes(query)),
  );
});

function loadSuccessScope(): "today" | "all" {
  return localStorage.getItem(SUCCESS_SCOPE_KEY) === "all" ? "all" : "today";
}

function applyDock(): void {
  dockOpen.value = !dockMedia.matches;
}

dockMedia.addEventListener("change", applyDock);

onMounted(() => {
  window.addEventListener("keydown", onKeydown);
  void refreshIdentity();
  void loadWatchPaths();
  void refreshSuccessTotals();
  void refreshWorkers();
  workerTimer = window.setInterval(() => {
    void refreshWorkers();
  }, 2000);
  void fetchSystemVersion().then((info) => {
    versionInfo.value = info;
  }).catch(() => undefined);
});

onUnmounted(() => {
  window.removeEventListener("keydown", onKeydown);
  dockMedia.removeEventListener("change", applyDock);
  if (workerTimer) {
    window.clearInterval(workerTimer);
    workerTimer = 0;
  }
});

onDeactivated(() => {
  showSessionForm.value = false;
  successOpen.value = false;
  workerModalOpen.value = false;
  policyModalOpen.value = false;
});

async function refreshSuccessTotals(): Promise<void> {
  try {
    const dataAll = await fetchSuccessPage("all", 1, 1);
    successAllTotal.value = dataAll.total;
  } catch {
    // 忽略异常
  }
}

function onSuccessScopeChange(value: "today" | "all"): void {
  successScope.value = value;
  localStorage.setItem(SUCCESS_SCOPE_KEY, value);
  if (successOpen.value) {
    successPage.value = 1;
    void loadSuccess();
  }
}

function toggleSuccessScope(event?: Event): void {
  if (event) event.stopPropagation();
  const next = successScope.value === "today" ? "all" : "today";
  onSuccessScopeChange(next);
}

async function loadSuccess(): Promise<void> {
  successLoading.value = true;
  try {
    const data = await fetchSuccessPage(successScope.value, successPage.value, successPageSize);
    successItems.value = data.items;
    successTotalRows.value = data.total;
    successPage.value = data.page;
    if (successScope.value === "all") {
      successAllTotal.value = data.total;
    }
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
  successSearchText.value = "";
}

async function shiftSuccessPage(step: number): Promise<void> {
  const next = successPage.value + step;
  if (next < 1 || next > successPages.value) return;
  successPage.value = next;
  await loadSuccess();
}

// ── 遥测与版本展示 ─────────────────────────────────────────────

const liveSpeed = computed(() => {
  const current = boardItems.value.find((item) => item.status === "uploading" || item.status === "assigned");
  if (!current) return "—";
  if (current.speed_bps > 0) return formatSpeed(current.speed_bps);
  return "测算中";
});

const versionLabel = computed(() => {
  const info = versionInfo.value;
  if (!info) return "v2.4.1 最新";
  const rev = shortRevision(info.current_version);
  if (info.status === "update") return "有更新";
  if (info.status === "dev") return "开发版";
  if (info.status === "staging") return rev ? `测试版 ${rev}` : "测试版";
  return rev ? `v${rev}` : (info.current_version || "最新版");
});

const versionTitle = computed(() => versionInfo.value?.commit_message || "点击查看系统版本状态");

function onVersionClick(): void {
  const info = versionInfo.value;
  if (!info) {
    ElMessageBox.alert("当前客户端运行正常，FastAPI 与 Telethon 引擎活跃运作中。", "版本检查", {
      confirmButtonText: "确定",
      type: "info",
    });
    return;
  }
  const rev = shortRevision(info.current_version);
  const statusMsg =
    info.status === "update"
      ? "发现有新版本可用！请关注 release 更新。"
      : info.status === "staging"
      ? "当前运行测试版分支 (Staging)"
      : "当前安装版本运行健康，无可用更新。";
  const commitMsg = info.commit_message ? `\n提交信息: ${info.commit_message}` : "";
  ElMessageBox.alert(
    `安装版本: ${info.current_version} (${rev ? `rev ${rev}` : info.status})\n${statusMsg}${commitMsg}`,
    "版本检查与状态",
    {
      confirmButtonText: "确定",
      type: info.status === "update" ? "warning" : "info",
    },
  );
}

const dockItems = computed(() =>
  watchPaths.value.map((path) => {
    const info = pathInfos.value.find((item) => samePath(item.path, path));
    const root = observerRoots.value.find((item) => samePath(item.path, path));
    const isRootEnabled = root ? root.enabled !== false : true;
    return {
      path,
      name: pathName(path),
      enabled: isRootEnabled,
      ok: root ? root.ok : (info ? info.ok : true),
      error: root?.error || info?.error || "",
      children: childRoutes(path),
    };
  }),
);

const activeRootsCount = computed(() => {
  if (!watchEnabled.value) return 0;
  return dockItems.value.filter((i) => i.enabled && i.ok).length;
});

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

function childRoutes(path: string): { path: string; label: string; enabled: boolean }[] {
  const key = normPath(path);
  return routes.value
    .filter((item) => {
      const child = normPath(item.path);
      if (child === key || !child.startsWith(`${key}/`)) return false;
      const owner = longestWatch(item.path);
      return owner != null && samePath(owner, path);
    })
    .map((item) => ({
      path: item.path,
      label: item.name || pathName(item.path),
      enabled: item.enabled !== false,
    }));
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
    observerRoots.value = settings.observer_roots ?? [];
    watchPaths.value = observerRoots.value.map((r) => r.path);
    pathInfos.value = settings.observer_path_infos ?? [];
    routes.value = settings.routes;
    watchEnabled.value = settings.watch_enabled !== false;
  } catch {
    watchPaths.value = [];
    pathInfos.value = [];
    routes.value = [];
    observerRoots.value = [];
  }
}

async function handleToggleGlobalWatch(): Promise<void> {
  if (togglingGlobalWatch.value) return;
  togglingGlobalWatch.value = true;
  try {
    const res = await toggleGlobalWatch();
    watchEnabled.value = res.watch_enabled;
    if (res.observer_roots) {
      observerRoots.value = res.observer_roots;
    }
    if (res.watch_enabled) {
      ElMessage.success("已开启全局自动监控，正在全量补扫各目录");
    } else {
      ElMessage.warning("已暂停全局自动监控，各目录已联动暂停");
    }
    await loadWatchPaths();
    await queryClient.invalidateQueries({ queryKey: ["fs-nodes"] });
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : "切换全局监控失败");
  } finally {
    togglingGlobalWatch.value = false;
  }
}

async function handleToggleRoot(path: string): Promise<void> {
  const key = normPath(path);
  if (togglingRoots.value.has(key)) return;
  togglingRoots.value.add(key);
  try {
    const res = await toggleRootPath(path);
    observerRoots.value = res.observer_roots;
    if (typeof res.watch_enabled === "boolean") {
      watchEnabled.value = res.watch_enabled;
    }
    const root = res.observer_roots.find((r) => samePath(r.path, path));
    const isNowOn = root ? root.enabled !== false : true;
    if (isNowOn) {
      ElMessage.success(`已恢复「${pathName(path)}」监控并执行补扫`);
    } else {
      ElMessage.info(`已暂停「${pathName(path)}」监控`);
    }
    await queryClient.invalidateQueries({ queryKey: ["fs-nodes"] });
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : "切换根目录监控失败");
  } finally {
    togglingRoots.value.delete(key);
  }
}

function isSubfolderEnabled(data: FsNode): boolean {
  const rule = routes.value.find((r) => samePath(r.path, data.path));
  if (rule) {
    return rule.enabled !== false;
  }
  if (data.current_route && typeof data.current_route.enabled === "boolean") {
    return data.current_route.enabled;
  }
  return true;
}

async function handleToggleSubfolder(path: string, _node?: any, data?: FsNode): Promise<void> {
  const key = normPath(path);
  if (togglingRoutes.value.has(key)) return;
  togglingRoutes.value.add(key);
  try {
    const res = await toggleRoutePath(path);
    routes.value = res.routes;
    const rule = res.routes.find((r) => samePath(r.path, path));
    const isNowOn = rule ? rule.enabled !== false : true;
    if (isNowOn) {
      ElMessage.success(`已恢复「${pathName(path)}」监控并开始补扫`);
    } else {
      ElMessage.warning(`已就地阻断「${pathName(path)}」，阻止落入父级规则`);
    }
    if (data) {
      if (!data.current_route) {
        data.current_route = { matched: true, is_explicit: !isNowOn, is_inherited: isNowOn, enabled: isNowOn };
      } else {
        data.current_route.enabled = isNowOn;
        data.current_route.is_inherited = isNowOn;
        data.current_route.is_explicit = !isNowOn;
      }
    }
    await queryClient.invalidateQueries({ queryKey: ["fs-nodes"] });
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : "切换子目录监控失败");
  } finally {
    togglingRoutes.value.delete(key);
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
  policyPath.value = path;
  policyModalOpen.value = true;
}

async function promptAddWatchPath(): Promise<void> {
  try {
    const { value } = await ElMessageBox.prompt(
      "请输入本地监控目录的绝对或相对路径",
      "添加监控路径",
      {
        confirmButtonText: "添加并生效",
        cancelButtonText: "取消",
        inputPlaceholder: "例如 D:\\Downloads 或 download",
        inputValidator: (val) => {
          if (!val || !val.trim()) return "路径不能为空";
          if (observerRoots.value.some((r) => samePath(r.path, val.trim()))) return "该路径已在监控列表中";
          return true;
        },
      },
    );
    if (value && value.trim()) {
      const trimmed = value.trim();
      const config = await fetchSettings();
      const roots = [...(config.observer_roots || [])];
      if (!roots.some((r) => samePath(r.path, trimmed))) {
        roots.push({
          path: trimmed,
          enabled: true,
          ok: true,
          error: "",
        });
        config.observer_roots = roots;
        if (config.observer_paths) {
          config.observer_paths = roots.map((r) => r.path);
        }
        if (!config.watch_enabled) {
          config.watch_enabled = true;
        }
        await saveSettings(config);
        await loadWatchPaths();
        await queryClient.invalidateQueries({ queryKey: ["fs-nodes"] });
        ElMessage.success(`已成功添加监控路径「${pathName(trimmed)}」，配置立即生效`);
      }
    }
  } catch {
    // 用户取消输入
  }
}

async function removeWatchPath(path: string): Promise<void> {
  const name = pathName(path);
  try {
    await ElMessageBox.confirm(
      `确定要删除监控路径「${name}」吗？\n\n完整路径：${path}\n\n确认后将立即从配置中移除并同步至后端，停止监听该目录。`,
      "二次确认：删除监控路径",
      {
        confirmButtonText: "确认删除",
        cancelButtonText: "取消",
        type: "warning",
        confirmButtonClass: "el-button--danger",
        distinguishCancelAndClose: true,
      },
    );
    const config = await fetchSettings();
    config.observer_roots = (config.observer_roots || []).filter((r) => !samePath(r.path, path));
    if (config.observer_paths) {
      config.observer_paths = config.observer_roots.map((r) => r.path);
    }
    const remainingActive = (config.observer_roots || []).filter((r) => r.enabled !== false).length;
    if (remainingActive === 0) {
      config.watch_enabled = false;
    }
    await saveSettings(config);
    expandedPaths.value.delete(normPath(path));
    await loadWatchPaths();
    await queryClient.invalidateQueries({ queryKey: ["fs-nodes"] });
    ElMessage.success(`已删除监控路径「${name}」，后端已同步生效`);
  } catch {
    // 用户取消删除
  }
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

function onUpdateWorkers(list: WorkerSnapshot[]): void {
  workers.value = list;
}

const activeWorkersCount = computed(() =>
  workers.value.filter((w) => w.enabled && w.running && w.accepting).length,
);

const totalWorkersCount = computed(() => workers.value.length);

function onKeydown(e: KeyboardEvent): void {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "b") {
    e.preventDefault();
    dockOpen.value = !dockOpen.value;
  } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "h") {
    e.preventDefault();
    openSuccess();
  } else if ((e.metaKey || e.ctrlKey) && e.key === ",") {
    e.preventDefault();
    openSettings("telegram");
  } else if (e.key === "Escape") {
    if (policyModalOpen.value) policyModalOpen.value = false;
    else if (workerModalOpen.value) workerModalOpen.value = false;
    else if (successOpen.value) closeSuccess();
    else if (settingsOpen.value) closeSettings();
    else if (showSessionForm.value) closeSessionForm();
  }
}

// 弹窗展开时锁定 body 滚动条
watch(
  [showSessionForm, successOpen, settingsOpen, workerModalOpen, policyModalOpen],
  ([sessionOpen, historyOpen, settings, workerOpen, policyOpen]) => {
    document.body.style.overflow = sessionOpen || historyOpen || settings || workerOpen || policyOpen ? "hidden" : "";
  },
);

watch(successCount, () => {
  void refreshSuccessTotals();
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
    <!-- ══════════════════════════════════════════════════════════
         固定顶栏 (Global Header & Telemetry Ribbon)
         - 版本提示按钮：补在遥测带的左侧，点击弹出版本检查
         - 遥测带已删除“并发分块”，展示实时速度、Worker在线数与上传数胶囊
         - 上传数胶囊：支持点击弹出历史流水，右侧带独立的“切换今天/总数”按钮
    ══════════════════════════════════════════════════════════ -->
    <header class="work-header">
      <!-- 区域 1：品牌与版本状态岛 -->
      <div class="header-lead">
        <!-- 侧栏收折按键 (32x32px 实体圆角按钮) -->
        <button
          type="button"
          class="sidebar-toggle-btn"
          :aria-expanded="dockOpen"
          title="展开/收起目录侧栏 (Ctrl+B)"
          @click="dockOpen = !dockOpen"
        >
          <svg class="toggle-icon" :class="{ rotated: !dockOpen }" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
          </svg>
        </button>

        <strong class="brand">Tg-up</strong>

        <!-- 版本提示按钮 -->
        <button
          v-if="versionLabel"
          type="button"
          class="version-pill"
          :class="versionInfo ? `tag-${versionInfo.status}` : 'tag-latest'"
          :title="versionTitle || '点击查看版本状态'"
          @click="onVersionClick"
        >
          <span class="version-dot"></span>
          <span>{{ versionLabel }}</span>
        </button>
      </div>

      <!-- 区域 2：只读遥测浮岛（实时传输仪表盘） -->
      <div class="telemetry-island">
        <div class="telemetry-badge" title="当前所有 Worker 的瞬时上传汇总速度">
          <svg class="telemetry-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8">
            <path d="M8.5 1.5L2.5 9h5l-1 5.5 6-7.5h-5l1-5.5z" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          <span class="telemetry-label">实时传输:</span>
          <b class="telemetry-val font-mono">{{ liveSpeed }}</b>
        </div>
      </div>

      <!-- 区域 3：控制中心交互岛（Worker 节点管理、上传流水、设置） -->
      <div class="header-action-island">
        <!-- ★★★ Worker 节点管理芯片按钮（微芯片图标 + 状态灯 + 独立管理操作胶囊） ★★★ -->
        <button
          type="button"
          class="worker-chip-btn"
          :class="{ 'has-offline': activeWorkersCount < totalWorkersCount }"
          title="点击打开 Worker 节点调度管理面板"
          @click="workerModalOpen = true"
        >
          <!-- 服务器微芯片图标 -->
          <span class="worker-chip-icon">
            <svg viewBox="0 0 16 16" fill="currentColor" width="13" height="13">
              <path d="M1.5 3A1.5 1.5 0 0 1 3 1.5h10A1.5 1.5 0 0 1 14.5 3v2A1.5 1.5 0 0 1 13 6.5H3A1.5 1.5 0 0 1 1.5 5V3zm0 6A1.5 1.5 0 0 1 3 7.5h10A1.5 1.5 0 0 1 14.5 9v2a1.5 1.5 0 0 1-1.5 1.5H3A1.5 1.5 0 0 1 1.5 11V9zM3 2.5a.5.5 0 0 0-.5.5v2a.5.5 0 0 0 .5.5h10a.5.5 0 0 0 .5-.5V3a.5.5 0 0 0-.5-.5H3zm0 6a.5.5 0 0 0-.5.5v2a.5.5 0 0 0 .5.5h10a.5.5 0 0 0 .5-.5V9a.5.5 0 0 0-.5-.5H3z"/>
              <circle cx="12" cy="4" r=".75" fill="currentColor"/>
              <circle cx="12" cy="10" r=".75" fill="currentColor"/>
            </svg>
          </span>
          <span class="worker-chip-status-dot"></span>
          <span class="worker-chip-label">Worker 在线</span>
          <b class="worker-chip-count font-mono">{{ activeWorkersCount }}/{{ totalWorkersCount }}</b>
          <span class="worker-chip-action-pill">管理</span>
        </button>

        <!-- 上传流水与口径切换复合胶囊 -->
        <div class="scope-island-pill">
          <button type="button" class="scope-island-view-btn" title="查看传输历史记录 (Ctrl+H)" @click="openSuccess">
            <svg class="scope-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6">
              <path d="M2.5 4.5h11M2.5 8h7M2.5 11.5h5" stroke-linecap="round"/>
            </svg>
            <span class="scope-island-label">{{ successScope === "today" ? "今日已传:" : "累计已传:" }}</span>
            <b class="scope-island-count font-mono">{{ successScope === "today" ? successCount : (successAllTotal || successCount) }}</b>
          </button>
          <span class="scope-island-divider"></span>
          <button type="button" class="scope-island-toggle-btn" title="切换今日 / 累计总数" @click.stop="toggleSuccessScope">
            {{ successScope === "today" ? "切总数 ▾" : "切今日 ▾" }}
          </button>
        </div>

        <!-- 偏好设置按钮 -->
        <button type="button" class="settings-pill-btn" title="系统偏好设置 (Ctrl+,)" @click="openSettings('telegram')">
          <svg class="settings-icon" viewBox="0 0 16 16" fill="currentColor" width="14" height="14">
            <path d="M8 4.754a3.246 3.246 0 1 0 0 6.492 3.246 3.246 0 0 0 0-6.492zM5.754 8a2.246 2.246 0 1 1 4.492 0 2.246 2.246 0 0 1-4.492 0z"/>
            <path d="M9.796 1.343c-.527-1.79-3.065-1.79-3.592 0l-.094.319a.873.873 0 0 1-1.255.52l-.292-.16c-1.64-.892-3.433.902-2.54 2.541l.159.292a.873.873 0 0 1-.52 1.255l-.319.094c-1.79.527-1.79 3.065 0 3.592l.319.094a.873.873 0 0 1 .52 1.255l-.16.292c-.892 1.64.901 3.434 2.541 2.54l.292-.159a.873.873 0 0 1 1.255.52l.094.319c.527 1.79 3.065 1.79 3.592 0l.094-.319a.873.873 0 0 1 1.255-.52l.292.16c1.64.893 3.434-.902 2.54-2.541l-.159-.292a.873.873 0 0 1 .52-1.255l.319-.094c1.79-.527 1.79-3.065 0-3.592l-.319-.094a.873.873 0 0 1-.52-1.255l.16-.292c.893-1.64-.902-3.433-2.541-2.54l-.292.159a.873.873 0 0 1-1.255-.52l-.094-.319z"/>
          </svg>
          <span>设置</span>
          <span v-if="settingsDirty" class="dirty-dot" aria-label="有未保存的修改"></span>
        </button>
      </div>
    </header>

    <!-- 配置警示横幅 -->
    <p v-if="configured === false" class="setup-banner">
      请先配置 API_ID / API_HASH，保存后当前进程即可使用。
      <button type="button" @click="openSettings('account')">去配置</button>
    </p>
    <p v-if="unmatchedCount > 0" class="unmatched-banner">
      {{ unmatchedCount }} 个文件未命中路由，未进入上传队列。在左侧目录的「策略」里指定群。
    </p>

    <!-- ══════════════════════════════════════════════════════════
         工作台主视窗：左右绝对等高、各自独立局部滑动，无整页滚动
    ══════════════════════════════════════════════════════════ -->
    <div class="work-body">
      <button
        v-if="dockOpen"
        type="button"
        class="dock-scrim"
        aria-label="关闭目录"
        @click="dockOpen = false"
      ></button>

      <!-- 左侧栏：监控目录舱（局部内部滑动） -->
      <aside class="dock" :class="{ shut: !dockOpen }">
        <div class="dock-head">
          <div class="dock-head-left">
            <h2>监控目录</h2>
            <span class="dock-count-badge" :class="{ 'is-paused': !watchEnabled }">
              {{ !watchEnabled ? "全局已暂停" : `${activeRootsCount} 处活跃` }}
            </span>
          </div>

          <div class="dock-head-right">
            <button
              v-if="expandedPaths.size > 0"
              type="button"
              class="dock-collapse-all-btn font-mono"
              title="收起所有展开的子目录树"
              @click="expandedPaths.clear()"
            >
              全部收起
            </button>
            <button type="button" class="dock-toggle-btn" title="收起目录 (Ctrl+B)" @click="dockOpen = false">
              收起
            </button>
          </div>
        </div>

        <!-- 目录卡片流：默认极简卡片，点击小三角或卡片原地展开子目录树 -->
        <div class="dock-list">
          <p v-if="dockItems.length === 0" class="dock-empty">
            还没有监听目录，请点击下方添加
          </p>

          <article
            v-for="item in dockItems"
            :key="item.path"
            class="dock-item"
            :class="{
              'is-expanded': isCardExpanded(item.path),
              'is-root-paused': !item.enabled || !watchEnabled,
            }"
          >
            <!-- 卡片头部标题行 (点击三角或标题行均可触发展开/收起) -->
            <div
              class="dock-row"
              :title="isCardExpanded(item.path) ? '点击折叠此目录文件树' : '点击展开此目录文件树'"
              @click="toggleCardExpand(item.path)"
            >
              <button
                type="button"
                class="dock-fold-arrow"
                :class="{ 'expanded': isCardExpanded(item.path) }"
                :title="isCardExpanded(item.path) ? '点击折叠此目录树' : '点击展开子文件树'"
                @click.stop="toggleCardExpand(item.path)"
              >
                {{ isCardExpanded(item.path) ? "▼" : "▶" }}
              </button>
              <span class="dock-dot" :class="{ bad: !item.ok }" :title="item.error || '可读取'"></span>
              <strong :title="item.path" class="dock-title-name">{{ item.name }}</strong>

              <div class="dock-actions" @click.stop>
                <el-switch
                  :model-value="item.enabled"
                  size="small"
                  class="dock-root-switch"
                  :disabled="togglingRoots.has(normPath(item.path))"
                  :title="item.enabled ? '点击暂停此根目录监控' : '点击开启此根目录监控并执行补扫'"
                  @change="handleToggleRoot(item.path)"
                />
                <button
                  type="button"
                  class="dock-policy-btn"
                  title="配置此目录整体交付规则"
                  @click="openPolicy(item.path)"
                >
                  策略
                </button>
                <button
                  type="button"
                  class="dock-delete-btn"
                  title="停止监听并从列表中移除此目录"
                  @click="removeWatchPath(item.path)"
                >
                  删除
                </button>
              </div>
            </div>

            <p class="dock-path font-mono" :title="item.path">{{ item.path }}</p>

            <!-- 未展开时：展示简洁的已配置专属子规则小胶囊 -->
            <div v-if="!isCardExpanded(item.path) && item.children.length" class="dock-children">
              <button
                v-for="child in item.children"
                :key="child.path"
                type="button"
                class="dock-child-chip"
                :class="{ 'is-child-disabled': !child.enabled }"
                :title="child.path"
                @click="openPolicy(child.path)"
              >
                {{ child.label }}{{ !child.enabled ? " (已阻断)" : "" }}
              </button>
            </div>

            <!-- 原地展开的子文件树 (Inline Accordion Tree) -->
            <div v-if="isCardExpanded(item.path)" class="card-inline-tree-container" @click.stop>
              <div class="inline-tree-toolbar">
                <span class="inline-tree-hint font-mono">子目录与文件穿透</span>
                <button
                  type="button"
                  class="inline-refresh-btn font-mono"
                  title="重新扫描并刷新此目录结构"
                  @click="refreshCardRoot(item.path)"
                >
                  🔄 刷新
                </button>
              </div>

              <el-tree
                :ref="(el: any) => setTreeRef(item.path, el)"
                lazy
                :load="(node: any, resolve: any) => loadCardFsNodes(item.path, node, resolve)"
                node-key="path"
                :props="{ label: 'name', isLeaf: (data: FsNode) => !data.is_dir }"
                class="dock-fs-tree"
              >
                <template #default="{ node, data }">
                  <div class="tree-node-row">
                    <div class="node-left" :class="{ 'is-dir': data.is_dir }">
                      <span v-if="data.is_dir" class="node-icon">📂</span>
                      <span v-else class="node-icon">📄</span>
                      <span
                        class="node-name truncate"
                        :class="{ 'is-folder': data.is_dir }"
                        :title="data.path"
                      >
                        {{ data.name }}
                      </span>
                      <!-- 状态胶囊跟随目录名称，不推挤右侧操作按钮 -->
                      <span
                        v-if="data.is_dir && !isSubfolderEnabled(data)"
                        class="node-route-badge blocked-rule"
                        title="此子目录监控已阻断，不会上传"
                      >
                        已阻断
                      </span>
                      <span
                        v-else-if="data.is_dir && data.current_route?.matched"
                        class="node-route-badge"
                        :class="{
                          'custom-rule': !data.current_route?.is_inherited,
                          'inherited-rule': data.current_route?.is_inherited,
                        }"
                        :title="data.current_route?.dest_name || ''"
                      >
                        {{ data.current_route?.is_inherited ? '继承' : '专属' }}: {{ data.current_route?.dest_name }}
                      </span>
                      <template v-if="!data.is_dir">
                        <span class="file-size font-mono">{{ formatBytes(data.size || 0) }}</span>
                        <span v-if="!data.supported_ext" class="file-status status-gray">未监听</span>
                        <span v-else-if="data.task_status === 'uploading'" class="file-status status-uploading">上传中</span>
                        <span v-else-if="data.task_status === 'success'" class="file-status status-success">已上传</span>
                        <span v-else-if="data.task_status === 'pending' || data.task_status === 'preparing'" class="file-status status-pending">排队中</span>
                        <span v-else-if="data.task_status === 'failed'" class="file-status status-failed" :title="data.task_error || '失败'">失败</span>
                        <span v-else-if="data.task_status === 'unmatched'" class="file-status status-unmatched">未命中</span>
                      </template>
                    </div>

                    <!-- 子文件夹操作区：保持完整操作性与位置绝对稳定性 -->
                    <div v-if="data.is_dir" class="node-right shrink-0" @click.stop>
                      <el-switch
                        :model-value="isSubfolderEnabled(data)"
                        size="small"
                        class="tree-dir-switch"
                        :disabled="togglingRoutes.has(normPath(data.path))"
                        :title="isSubfolderEnabled(data) ? '点击阻断此子目录监控' : '点击恢复此子目录监控并补扫'"
                        @change="handleToggleSubfolder(data.path, node, data)"
                      />

                      <button
                        type="button"
                        class="tree-policy-btn"
                        title="为此子目录单独配置交付规则"
                        @click="openPolicy(data.path)"
                      >
                        策略
                      </button>
                      <button
                        v-if="data.current_route?.matched && !data.current_route?.is_inherited && !data.current_route?.auto_blocked && isSubfolderEnabled(data)"
                        type="button"
                        class="tree-inherit-btn"
                        title="清除专属规则，恢复跟随父级"
                        @click="restoreSubfolderInheritance(data.path)"
                      >
                        恢复继承
                      </button>
                      <button
                        type="button"
                        class="tree-refresh-icon-btn"
                        title="刷新此子目录"
                        @click="refreshCardFsNode(node, data)"
                      >
                        🔄
                      </button>
                    </div>
                  </div>
                </template>
              </el-tree>
            </div>
          </article>
        </div>

        <!-- 侧栏底部：双层布局（🚀 手动投递主按钮 + 并列的管理开关） -->
        <div class="dock-bottom-bar">
          <button
            type="button"
            class="dock-bottom-btn manual-dispatch-btn"
            title="打开手动投递窗口，勾选文件立即投递上传"
            @click="manualDispatchOpen = true"
          >
            🚀 手动投递任务
          </button>
          <div class="dock-bottom-sub-row">
            <button type="button" class="dock-bottom-btn add-path-btn" @click="promptAddWatchPath">
              + 添加监控路径
            </button>
            <button
              type="button"
              class="dock-bottom-btn global-toggle-btn"
              :class="{ 'is-running': watchEnabled, 'is-paused': !watchEnabled }"
              :disabled="togglingGlobalWatch"
              :title="watchEnabled ? '点击一键暂停全局自动监控' : '点击一键开启全局自动监控并全量补扫'"
              @click="handleToggleGlobalWatch"
            >
              <span class="global-watch-dot" :class="{ 'on': watchEnabled }"></span>
              <span>{{ watchEnabled ? "全局监控中" : "监控已暂停" }}</span>
            </button>
          </div>
        </div>
      </aside>

      <!-- 右侧主区：多维任务调度看板 -->
      <TaskBoard class="board-pane" :items="boardItems" />
    </div>

    <!-- ══════════════════════════════════════════════════════════
         系统配置模态弹窗 (In-Place Modal)
    ══════════════════════════════════════════════════════════ -->
    <div v-show="settingsOpen" class="settings-layer" @click.self="closeSettings">
      <div class="settings-modal" role="dialog" aria-label="系统配置" @click.stop>
        <header class="settings-head">
          <div class="settings-head-left">
            <h2>系统配置 (Settings)</h2>
            <span v-if="settingsDirty" class="unsaved-badge">有未保存更改</span>
          </div>
          <div class="settings-head-right">
            <button
              type="button"
              class="save-toml-btn"
              :disabled="!settingsDirty || tomlSaving"
              title="保存全部改动至 upload.toml"
              @click="handleSaveToml"
            >
              <span v-if="tomlSaving" class="btn-spinner"></span>
              <span>保存到 upload.toml</span>
            </button>
            <button type="button" class="modal-close-btn" title="关闭设置 (Esc)" @click="closeSettings">✕</button>
          </div>
        </header>
        <SettingsPage
          ref="settingsRef"
          embedded
          @update:dirty="settingsDirty = $event"
          @section="onSettingsSection"
        />
      </div>
    </div>

    <!-- 添加 Session 弹窗 -->
    <Teleport to="body">
      <div v-if="showSessionForm" class="session-overlay" @click.self="closeSessionForm">
        <div class="session-modal" @click.stop>
          <SessionPanel @close="closeSessionForm" />
        </div>
      </div>
    </Teleport>

    <!-- ══════════════════════════════════════════════════════════
         传输历史记录模态弹窗 (History Stream Modal)
    ══════════════════════════════════════════════════════════ -->
    <Teleport to="body">
      <div v-if="successOpen" class="session-overlay" @click.self="closeSuccess">
        <div class="session-modal success-modal" role="dialog" aria-label="传输历史记录" @click.stop>
          <header class="success-head">
            <div class="success-head-left">
              <span class="success-badge-dot"></span>
              <div>
                <h2>传输历史记录 (Transfer Stream)</h2>
                <p class="success-sub">
                  {{ successScope === 'today' ? '今日已送达' : '累计已送达' }} {{ successTotalRows }} 个文件 · 本地数据库驱动
                </p>
              </div>
            </div>
            <div class="success-head-right">
              <div class="success-switch">
                <button type="button" :class="{ on: successScope === 'today' }" @click="onSuccessScopeChange('today')">今日</button>
                <button type="button" :class="{ on: successScope === 'all' }" @click="onSuccessScopeChange('all')">累计</button>
              </div>
              <button type="button" class="modal-close-btn" title="关闭 (Esc)" @click="closeSuccess">✕</button>
            </div>
          </header>

          <div class="success-search-bar">
            <input
              v-model="successSearchText"
              type="text"
              placeholder="按文件名或目录过滤历史..."
              class="success-search-input"
            />
          </div>

          <div class="success-body">
            <p v-if="successLoading" class="success-empty">正在读取历史流水...</p>
            <p v-else-if="filteredSuccessItems.length === 0" class="success-empty">没有匹配的成功记录</p>
            <table v-else class="success-table">
              <thead>
                <tr>
                  <th>文件名</th>
                  <th>源监控路径</th>
                  <th>文件大小</th>
                  <th>传输状态</th>
                  <th>完成时间</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in filteredSuccessItems" :key="item.id">
                  <td class="col-file" :title="item.file_name">
                    <span class="file-name">{{ item.file_name }}</span>
                    <span v-if="item.part_label" class="part-chip">{{ item.part_label }}</span>
                  </td>
                  <td class="col-folder font-mono" :title="item.folder_name || '根目录'">{{ item.folder_name || "—" }}</td>
                  <td class="col-size font-mono">{{ item.file_size > 0 ? formatBytes(item.file_size) : "—" }}</td>
                  <td class="col-status"><span class="status-pill ok">已送达</span></td>
                  <td class="col-time font-mono">{{ item.finished_at || "—" }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <footer class="success-pager">
            <span class="pager-stat">显示第 {{ successPage }} / {{ successPages }} 页 · 共 {{ successTotalRows }} 条记录</span>
            <div class="pager-actions">
              <button type="button" :disabled="successPage <= 1 || successLoading" @click="shiftSuccessPage(-1)">上一页</button>
              <span class="page-current font-mono">{{ successPage }}</span>
              <button type="button" :disabled="successPage >= successPages || successLoading" @click="shiftSuccessPage(1)">下一页</button>
            </div>
          </footer>
        </div>
      </div>
    </Teleport>

    <!-- ══════════════════════════════════════════════════════════
         Worker 节点调度管理模态弹窗 (由顶栏「Worker 在线」呼出)
    ══════════════════════════════════════════════════════════ -->
    <Teleport to="body">
      <div v-if="workerModalOpen" class="settings-layer" @click.self="workerModalOpen = false">
        <div class="worker-modal panel-card" role="dialog" aria-label="Worker 节点调度管理" @click.stop>
          <header class="worker-modal-head">
            <div class="worker-modal-head-left">
              <span class="worker-badge-dot"></span>
              <div>
                <h2>Worker 节点调度管理</h2>
                <p class="worker-sub">当前在线 {{ activeWorkersCount }} / {{ totalWorkersCount }} · 负责并发分块与个人号直传</p>
              </div>
            </div>
            <div class="worker-modal-head-right">
              <button type="button" class="add-session-btn" @click="openSessionForm">
                + 添加 Session
              </button>
              <button type="button" class="modal-close-btn" title="关闭 (Esc)" @click="workerModalOpen = false">✕</button>
            </div>
          </header>
          <div class="worker-modal-body">
            <WorkerPanel @add="openSessionForm" @update-workers="onUpdateWorkers" />
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 路径交付策略模态弹窗 (Directory Policy Modal) -->
    <DirectoryPolicyModal
      v-model="policyModalOpen"
      :path="policyPath"
      @saved="onPolicySaved"
    />

    <!-- 手动投递任务模态弹窗 (Manual Dispatch Modal) -->
    <ManualDispatchModal
      v-model="manualDispatchOpen"
      @dispatched="onManualDispatched"
    />
  </div>
</template>

<style scoped>
.workbench {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  height: 100%;
  max-height: 100dvh;
  overflow: hidden;
  background: var(--bg, #f5f7f6);
}

.work-header {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-shrink: 0;
  min-height: 52px;
  padding: 8px 18px;
  background: var(--surface, #ffffff);
  border-bottom: 1px solid var(--border, rgba(0, 0, 0, 0.08));
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.02);
}

.header-lead {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.sidebar-toggle-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm, 8px);
  border: 1px solid var(--border, rgba(0, 0, 0, 0.1));
  background: #f9fafb;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s ease;
}

.sidebar-toggle-btn:hover {
  background: #f3f4f6;
  color: var(--text);
  border-color: rgba(0, 0, 0, 0.15);
}

.toggle-icon {
  width: 15px;
  height: 15px;
  transition: transform 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

.toggle-icon.rotated {
  transform: rotate(180deg);
}

.brand {
  font-size: 16.5px;
  font-weight: 800;
  letter-spacing: -0.025em;
  color: var(--text, #111827);
  padding-right: 10px;
  border-right: 1px solid var(--border-light, rgba(0, 0, 0, 0.08));
}

.version-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: var(--radius-full, 9999px);
  border: 1px solid var(--border-light, rgba(0, 0, 0, 0.08));
  background: #f3f4f6;
  color: var(--text-secondary);
  font-size: 12px;
  font-family: ui-monospace, "SF Mono", monospace;
  cursor: pointer;
  transition: all 0.15s ease;
}

.version-pill:hover {
  background: #e5e7eb;
  color: var(--text);
}

.version-dot {
  width: 6.5px;
  height: 6.5px;
  border-radius: var(--radius-full, 9999px);
  background: var(--accent, #28995a);
}

.version-pill.tag-update .version-dot {
  background: #ef4444;
}

.version-pill.tag-staging .version-dot {
  background: #f59e0b;
}

/* 区域 2：只读遥测浮岛（实时传输仪表盘） */
.telemetry-island {
  display: flex;
  align-items: center;
  margin-left: auto;
  min-width: 0;
}

.telemetry-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 12px;
  background: #f8fafc;
  border: 1px solid var(--border, rgba(0, 0, 0, 0.08));
  border-radius: var(--radius-sm, 8px);
  user-select: none;
}

.telemetry-icon {
  width: 14px;
  height: 14px;
  color: #64748b;
  flex-shrink: 0;
}

.telemetry-label {
  color: var(--text-secondary);
  font-size: 12.5px;
}

.telemetry-val {
  color: var(--text);
  font-size: 13.5px;
  font-weight: 700;
}

/* 区域 3：控制中心交互岛 */
.header-action-island {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

/* ★★★ Worker 节点管理芯片按钮（微芯片图标 + 状态灯 + 独立管理操作胶囊） ★★★ */
.worker-chip-btn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 32px;
  padding: 0 6px 0 10px;
  background: #f0fdf4;
  border: 1.2px solid #86efac;
  border-radius: var(--radius-sm, 8px);
  cursor: pointer;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
  transition: all 0.18s cubic-bezier(0.16, 1, 0.3, 1);
}

.worker-chip-btn:hover {
  background: #dcfce7;
  border-color: #4ade80;
  box-shadow: 0 3px 8px rgba(34, 197, 94, 0.2);
  transform: translateY(-1px);
}

.worker-chip-btn:active {
  transform: scale(0.98);
}

.worker-chip-btn.has-offline {
  background: #fffbeb;
  border-color: #fde68a;
}

.worker-chip-btn.has-offline:hover {
  background: #fef3c7;
  border-color: #f59e0b;
}

.worker-chip-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #16a34a;
}

.worker-chip-btn.has-offline .worker-chip-icon {
  color: #d97706;
}

.worker-chip-status-dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full, 9999px);
  background: #22c55e;
  animation: pulse-glow 2s infinite;
}

.worker-chip-btn.has-offline .worker-chip-status-dot {
  background: #f59e0b;
  animation: pulse-glow-warn 2s infinite;
}

.worker-chip-label {
  font-size: 13px;
  font-weight: 600;
  color: #166534;
}

.worker-chip-btn.has-offline .worker-chip-label {
  color: #92400e;
}

.worker-chip-count {
  font-size: 13.5px;
  font-weight: 800;
  color: #15803d;
}

.worker-chip-btn.has-offline .worker-chip-count {
  color: #b45309;
}

.worker-chip-action-pill {
  display: inline-flex;
  align-items: center;
  font-size: 11.5px;
  font-weight: 700;
  color: #16a34a;
  background: #ffffff;
  border: 1px solid #bbf7d0;
  padding: 2px 7px;
  border-radius: 6px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}

.worker-chip-btn.has-offline .worker-chip-action-pill {
  color: #b45309;
  border-color: #fde68a;
}

/* 上传流水复合胶囊 */
.scope-island-pill {
  display: inline-flex;
  align-items: center;
  height: 32px;
  background: #ffffff;
  border: 1px solid var(--border, rgba(0, 0, 0, 0.12));
  border-radius: var(--radius-sm, 8px);
  padding: 0 4px 0 10px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.03);
  transition: all 0.15s ease;
}

.scope-island-pill:hover {
  border-color: rgba(40, 153, 90, 0.4);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.05);
}

.scope-island-view-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  border: 0;
  background: transparent;
  padding: 0 6px 0 0;
  color: var(--text);
  cursor: pointer;
}

.scope-icon {
  width: 14px;
  height: 14px;
  color: #64748b;
  flex-shrink: 0;
}

.scope-island-label {
  font-size: 13px;
  color: var(--text-secondary);
}

.scope-island-count {
  font-size: 14px;
  font-weight: 800;
  color: var(--text);
}

.scope-island-divider {
  width: 1px;
  height: 14px;
  background: var(--border, rgba(0, 0, 0, 0.1));
  margin: 0 4px;
}

.scope-island-toggle-btn {
  border: 0;
  background: #f8fafc;
  border-radius: 6px;
  padding: 3px 8px;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s ease;
}

.scope-island-toggle-btn:hover {
  background: #f1f5f9;
  color: var(--text);
}

/* 偏好设置按钮 */
.settings-pill-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 12px;
  background: #ffffff;
  border: 1px solid var(--border, rgba(0, 0, 0, 0.12));
  border-radius: var(--radius-sm, 8px);
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text);
  cursor: pointer;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.03);
  transition: all 0.15s ease;
  position: relative;
}

.settings-pill-btn:hover {
  background: #f8fafc;
  border-color: rgba(0, 0, 0, 0.18);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.05);
}

.settings-icon {
  color: #64748b;
}

.dirty-dot {
  width: 7px;
  height: 7px;
  border-radius: var(--radius-full, 9999px);
  background: var(--warn, #d97706);
}

.setup-banner,
.unmatched-banner {
  margin: 8px 14px 0;
  padding: 8px 12px;
  border-radius: var(--radius-md, 10px);
  font-size: 12.5px;
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
  font-weight: 600;
  cursor: pointer;
}

.unmatched-banner {
  border: 1px solid #f3d7a1;
  background: #fdf5ea;
  color: var(--warn);
}

/* ══════════════════════════════════════════════════════════
   左右绝对等高与局部独立滑动结构
══════════════════════════════════════════════════════════ */
.work-body {
  position: relative;
  display: flex;
  flex: 1;
  min-height: 0;
  align-items: stretch;
  gap: 12px;
  padding: 12px;
  overflow: hidden;
}

.dock {
  width: 290px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-self: stretch;
  min-height: 0;
  height: 100%;
  padding: 12px;
  background: var(--surface, #ffffff);
  border: 1px solid var(--border, rgba(0, 0, 0, 0.08));
  border-radius: var(--radius-lg, 14px);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.02);
  overflow: hidden;
  transition: width 0.22s cubic-bezier(0.16, 1, 0.3, 1), padding 0.22s ease;
}

.dock.shut {
  display: none;
}

.dock-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border-light, rgba(0, 0, 0, 0.05));
  flex-shrink: 0;
}

.dock-head-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.dock-head h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--text);
  letter-spacing: -0.01em;
}

.dock-count-badge {
  padding: 2px 7px;
  border-radius: var(--radius-sm, 6px);
  background: rgba(40, 153, 90, 0.1);
  color: var(--accent, #28995a);
  font-size: 12px;
  font-weight: 600;
  transition: all 0.15s ease;
}

.dock-count-badge.is-paused {
  background: rgba(239, 68, 68, 0.1);
  color: #ef4444;
}

.dock-toggle-btn {
  border: 0;
  background: transparent;
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
}

.dock-toggle-btn:hover {
  color: var(--text);
}

.dock-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 0;
}

.dock-collapse-all-btn {
  border: 1px solid rgba(0, 0, 0, 0.08);
  background: #f3f4f6;
  color: var(--text-secondary, #6b7280);
  font-size: 11.5px;
  padding: 2px 7px;
  border-radius: var(--radius-sm, 6px);
  cursor: pointer;
  transition: all 0.15s ease;
}

.dock-collapse-all-btn:hover {
  background: #e5e7eb;
  color: var(--text, #111827);
}

.dock-item {
  padding: 10px;
  border: 1px solid var(--border-light, rgba(0, 0, 0, 0.06));
  border-radius: var(--radius-md, 10px);
  background: #f9fafb;
  transition: all 0.18s cubic-bezier(0.16, 1, 0.3, 1);
}

.dock-item:hover {
  border-color: rgba(0, 0, 0, 0.14);
}

.dock-item.is-expanded {
  background: #ffffff;
  border-color: rgba(40, 153, 90, 0.35);
  box-shadow: 0 4px 16px -2px rgba(40, 153, 90, 0.08), 0 1px 3px rgba(0, 0, 0, 0.04);
}

.dock-item.is-root-paused {
  opacity: 0.72;
  background: #f8fafc;
  border-style: dashed;
}

.dock-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  cursor: pointer;
}

.dock-fold-arrow {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border: 0;
  background: transparent;
  color: #9ca3af;
  font-size: 11px;
  cursor: pointer;
  border-radius: var(--radius-sm, 6px);
  transition: all 0.15s ease;
  flex-shrink: 0;
  user-select: none;
}

.dock-fold-arrow:hover {
  background: rgba(0, 0, 0, 0.05);
  color: var(--text, #111827);
}

.dock-fold-arrow.expanded {
  color: var(--accent, #28995a);
}

.dock-title-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text);
}

.dock-actions {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-shrink: 0;
}

.dock-root-switch,
.tree-dir-switch {
  display: inline-flex;
  align-items: center;
  line-height: 1;
}

.dock-root-switch :deep(.el-switch__core),
.tree-dir-switch :deep(.el-switch__core) {
  --el-switch-on-color: var(--accent, #28995a);
  --el-switch-off-color: #d1d5db;
  height: 16px;
  min-width: 28px;
  width: 28px;
}

.dock-root-switch :deep(.el-switch__action),
.tree-dir-switch :deep(.el-switch__action) {
  width: 12px;
  height: 12px;
}

.dock-policy-btn {
  padding: 4px 10px;
  border-radius: var(--radius-sm, 6px);
  border: 1px solid var(--border, rgba(0, 0, 0, 0.12));
  background: #ffffff;
  color: var(--text);
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.dock-policy-btn:hover {
  border-color: #3b82f6;
  color: #2563eb;
}

.dock-delete-btn {
  padding: 4px 10px;
  border-radius: var(--radius-sm, 6px);
  border: 1px solid #ef4444;
  background: #ef4444;
  color: #ffffff;
  font-size: 12.5px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
  box-shadow: 0 1px 2px rgba(239, 68, 68, 0.2);
}

.dock-delete-btn:hover {
  background: #dc2626;
  border-color: #dc2626;
  box-shadow: 0 2px 4px rgba(220, 38, 38, 0.25);
}

.dock-delete-btn:active {
  background: #b91c1c;
  border-color: #b91c1c;
}

.dock-dot {
  width: 7px;
  height: 7px;
  border-radius: var(--radius-full, 9999px);
  background: var(--accent, #28995a);
  flex-shrink: 0;
}

.dock-dot.bad {
  background: var(--warn, #d97706);
}

.dock-path {
  margin: 4px 0 0;
  color: var(--text-secondary);
  font-size: 12.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dock-children {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 6px;
}

.dock-child-chip {
  padding: 3px 8px;
  border-radius: var(--radius-sm, 6px);
  border: 1px solid rgba(59, 130, 246, 0.2);
  background: rgba(59, 130, 246, 0.06);
  color: #2563eb;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
}

.dock-child-chip.is-child-disabled {
  border-color: rgba(239, 68, 68, 0.25);
  background: rgba(239, 68, 68, 0.06);
  color: #dc2626;
}

/* ── 原地展开的子目录树容器与工具栏 ── */
.card-inline-tree-container {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed rgba(0, 0, 0, 0.08);
  display: flex;
  flex-direction: column;
  gap: 4px;
  animation: fadeInTree 0.18s ease;
}

@keyframes fadeInTree {
  from {
    opacity: 0;
    transform: translateY(-3px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.inline-tree-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 2px 4px;
}

.inline-tree-hint {
  font-size: 12.5px;
  color: #9ca3af;
  letter-spacing: 0.01em;
}

.inline-refresh-btn {
  border: 0;
  background: transparent;
  color: var(--accent, #28995a);
  font-size: 12.5px;
  font-weight: 600;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: var(--radius-sm, 6px);
  transition: background 0.12s ease;
}

.inline-refresh-btn:hover {
  background: rgba(40, 153, 90, 0.08);
}

/* ── 嵌入卡片内的 Element Plus 文件树 ── */
.dock-fs-tree {
  background: transparent !important;
  font-size: 13.5px;
  overflow-x: hidden;
}

.dock-fs-tree :deep(.el-tree-node__content) {
  height: auto;
  min-height: 30px;
  padding: 3px 0;
  border-radius: var(--radius-sm, 6px);
  transition: background 0.12s ease;
}

.dock-fs-tree :deep(.el-tree-node__content:hover) {
  background: rgba(0, 0, 0, 0.04);
}

.dock-fs-tree :deep(.el-tree-node__expand-icon) {
  padding: 3px 2px;
  font-size: 12px;
  color: #9ca3af;
}

.dock-fs-tree :deep(.el-tree-node__expand-icon.is-leaf) {
  color: transparent;
  cursor: default;
}

.tree-node-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  min-width: 0;
  padding-right: 4px;
  gap: 6px;
}

.node-left {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  flex: 1;
}

.node-icon {
  font-size: 14px;
  flex-shrink: 0;
}

.node-name {
  font-size: 13.5px;
  color: #1f2937;
  line-height: 1.35;
}

.node-name.is-folder {
  font-weight: 600;
  color: #374151;
}

.node-name.truncate {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-size {
  font-size: 12.5px;
  color: #9ca3af;
  flex-shrink: 0;
}

.file-status {
  font-size: 12px;
  padding: 2px 6px;
  border-radius: var(--radius-sm, 6px);
  font-weight: 600;
  flex-shrink: 0;
  white-space: nowrap;
}

.status-uploading {
  background: rgba(40, 153, 90, 0.1);
  color: #28995a;
}

.status-success {
  background: #ecfdf5;
  color: #059669;
}

.status-pending {
  background: #fef3c7;
  color: #b45309;
}

.status-failed {
  background: #fee2e2;
  color: #b91c1c;
}

.status-unmatched {
  background: #f3f4f6;
  color: #6b7280;
}

.status-gray {
  background: #f3f4f6;
  color: #9ca3af;
}

.node-right {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.node-route-badge {
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, monospace;
  padding: 2px 6px;
  border-radius: var(--radius-sm, 6px);
  white-space: nowrap;
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 1.25;
  flex-shrink: 0;
}

.node-route-badge.custom-rule {
  background: #eff6ff;
  border: 1px solid rgba(59, 130, 246, 0.25);
  color: #2563eb;
  font-weight: 600;
}

.node-route-badge.inherited-rule {
  background: #f3f4f6;
  border: 1px solid rgba(107, 114, 128, 0.2);
  color: #6b7280;
}

.node-route-badge.blocked-rule {
  background: #fef2f2;
  border: 1px solid rgba(239, 68, 68, 0.25);
  color: #dc2626;
  font-weight: 600;
}

.tree-policy-btn {
  padding: 3px 8px;
  border-radius: var(--radius-sm, 6px);
  border: 1px solid rgba(0, 0, 0, 0.12);
  background: #ffffff;
  color: #374151;
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.12s ease;
  white-space: nowrap;
}

.tree-policy-btn:hover {
  background: #eff6ff;
  color: #2563eb;
  border-color: #93c5fd;
}

.tree-inherit-btn {
  padding: 3px 7px;
  border-radius: var(--radius-sm, 6px);
  border: 1px solid rgba(239, 68, 68, 0.25);
  background: #fff5f5;
  color: #dc2626;
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.12s ease;
  white-space: nowrap;
}

.tree-inherit-btn:hover {
  background: #fee2e2;
  border-color: #ef4444;
}

.tree-refresh-icon-btn {
  border: 0;
  background: transparent;
  font-size: 12px;
  cursor: pointer;
  padding: 2px 4px;
  border-radius: var(--radius-sm, 6px);
  line-height: 1;
}

.tree-refresh-icon-btn:hover {
  background: #e5e7eb;
}

/* ══════════════════════════════════════════════════════════
   Worker 节点调度管理模态弹窗
══════════════════════════════════════════════════════════ */
.worker-modal {
  display: flex;
  flex-direction: column;
  width: min(760px, 100%);
  max-height: min(88dvh, 680px);
  height: min(88dvh, 680px);
  padding: 16px 20px;
  background: var(--surface, #ffffff);
  border-radius: var(--radius-xl, 16px);
  border: 1px solid var(--border, rgba(0, 0, 0, 0.08));
  box-shadow: 0 24px 64px -12px rgba(18, 30, 20, 0.24);
  overflow: hidden;
}

.worker-modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-light, rgba(0, 0, 0, 0.06));
  flex-shrink: 0;
}

.worker-modal-head-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.worker-badge-dot {
  width: 9px;
  height: 9px;
  border-radius: var(--radius-full, 9999px);
  background: var(--accent, #28995a);
  flex-shrink: 0;
}

.worker-modal-head h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: var(--text);
}

.worker-sub {
  margin: 2px 0 0;
  font-size: 12.5px;
  color: var(--text-secondary);
}

.worker-modal-head-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.add-session-btn {
  padding: 6px 14px;
  border-radius: var(--radius-sm, 6px);
  border: 0;
  background: var(--accent, #28995a);
  color: #ffffff;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.add-session-btn:hover {
  background: #22834d;
}

.worker-modal-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 8px 0;
}

.worker-modal :deep(.worker-card) {
  border: 0;
  box-shadow: none;
  background: transparent;
}

.worker-modal :deep(.el-card__header) {
  display: none;
}

.worker-modal :deep(.el-card__body) {
  padding: 4px 0 0;
}

.dock-bottom-bar {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 6px;
  flex-shrink: 0;
}

.manual-dispatch-btn {
  width: 100%;
  background: var(--accent, #28995a);
  color: #ffffff;
  border: 0;
  font-size: 13.5px;
  font-weight: 700;
  padding: 9px 12px;
  border-radius: var(--radius-md, 10px);
  box-shadow: 0 2px 6px rgba(40, 153, 90, 0.28);
  transition: all 0.16s cubic-bezier(0.16, 1, 0.3, 1);
}

.manual-dispatch-btn:hover:not(:disabled) {
  background: #22834d;
  box-shadow: 0 4px 12px rgba(40, 153, 90, 0.35);
}

.dock-bottom-sub-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.dock-bottom-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 10px;
  border-radius: var(--radius-md, 10px);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.16s cubic-bezier(0.16, 1, 0.3, 1);
  white-space: nowrap;
  user-select: none;
}

.dock-bottom-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.add-path-btn {
  border: 1px dashed var(--border, rgba(0, 0, 0, 0.15));
  background: #ffffff;
  color: var(--text-secondary, #6b7280);
}

.add-path-btn:hover:not(:disabled) {
  border-color: var(--accent, #28995a);
  color: var(--accent, #28995a);
  background: rgba(40, 153, 90, 0.04);
}

.global-toggle-btn {
  border: 1px solid var(--border, rgba(0, 0, 0, 0.1));
  background: #ffffff;
}

.global-toggle-btn.is-running {
  border-color: rgba(40, 153, 90, 0.28);
  background: rgba(40, 153, 90, 0.06);
  color: #1b7a42;
}

.global-toggle-btn.is-running:hover:not(:disabled) {
  background: rgba(40, 153, 90, 0.12);
  border-color: rgba(40, 153, 90, 0.45);
}

.global-toggle-btn.is-paused {
  border-color: rgba(239, 68, 68, 0.28);
  background: rgba(239, 68, 68, 0.05);
  color: #dc2626;
}

.global-toggle-btn.is-paused:hover:not(:disabled) {
  background: rgba(239, 68, 68, 0.1);
  border-color: rgba(239, 68, 68, 0.45);
}

.global-watch-dot {
  width: 7px;
  height: 7px;
  border-radius: var(--radius-full, 9999px);
  background: #ef4444;
  box-shadow: 0 0 0 2px rgba(239, 68, 68, 0.15);
  flex-shrink: 0;
  transition: all 0.2s ease;
}

.global-watch-dot.on {
  background: #10b981;
  box-shadow: 0 0 0 2px rgba(16, 185, 129, 0.2);
}

.board-pane {
  flex: 1;
  min-width: 0;
  align-self: stretch;
  min-height: 0;
  height: 100%;
  overflow: hidden;
}

.dock-scrim {
  display: none;
}

/* ══════════════════════════════════════════════════════════
   模态弹窗通用质感 (Apple 润白微磨砂)
══════════════════════════════════════════════════════════ */
.settings-layer,
.session-overlay {
  position: fixed;
  inset: 0;
  z-index: 1800;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: rgba(18, 24, 20, 0.36);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}

.session-overlay {
  z-index: 2000;
}

.settings-modal {
  display: flex;
  flex-direction: column;
  width: min(920px, calc(100vw - 32px));
  height: min(640px, calc(100vh - 40px));
  max-height: min(640px, calc(100vh - 40px));
  background: #ffffff;
  border-radius: var(--radius-xl, 16px);
  border: 1px solid rgba(0, 0, 0, 0.1);
  box-shadow: 0 24px 64px -12px rgba(18, 30, 20, 0.24);
  overflow: hidden;
  padding: 0;
}

.settings-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 13px 20px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
  background: #ffffff;
  flex-shrink: 0;
}

.settings-head-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.settings-head-left h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: #111827;
}

.unsaved-badge {
  padding: 2px 7px;
  border-radius: var(--radius-sm, 6px);
  background: #fef3c7;
  color: #b45309;
  font-size: 12px;
  font-weight: 600;
  animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}

.settings-head-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.save-toml-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 16px;
  border: 0;
  border-radius: var(--radius-sm, 6px);
  background: #28995a;
  color: #ffffff;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
}

.save-toml-btn:hover:not(:disabled) {
  background: #23864f;
}

.save-toml-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  background: #9ca3af;
}

.btn-spinner {
  width: 12px;
  height: 12px;
  border: 2px solid #ffffff;
  border-top-color: transparent;
  border-radius: var(--radius-full, 9999px);
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@keyframes pulse {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.5;
  }
}

.modal-close-btn {
  width: 30px;
  height: 30px;
  border-radius: var(--radius-sm, 6px);
  border: 0;
  background: transparent;
  color: #9ca3af;
  font-size: 16px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s ease;
}

.modal-close-btn:hover {
  background: #f3f4f6;
  color: #374151;
}

.settings-modal :deep(.settings) {
  flex: 1;
  min-height: 0;
}

.session-modal {
  width: min(560px, 100%);
  max-height: min(90vh, 840px);
  overflow: auto;
  border-radius: var(--radius-xl, 16px);
  background: var(--surface);
  box-shadow: 0 24px 64px -12px rgba(18, 30, 20, 0.24);
}

/* ══════════════════════════════════════════════════════════
   传输历史记录模态弹窗 (宽表流水与分页器)
══════════════════════════════════════════════════════════ */
.success-modal {
  display: flex;
  flex-direction: column;
  width: min(960px, 100%);
  max-height: min(88dvh, 720px);
  height: min(88dvh, 720px);
  padding: 16px 20px;
  border-radius: var(--radius-xl, 16px);
  overflow: hidden;
}

.success-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-light, rgba(0, 0, 0, 0.06));
  flex-shrink: 0;
}

.success-head-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.success-badge-dot {
  width: 9px;
  height: 9px;
  border-radius: var(--radius-full, 9999px);
  background: var(--accent, #28995a);
  flex-shrink: 0;
}

.success-head h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: var(--text);
}

.success-sub {
  margin: 2px 0 0;
  font-size: 12.5px;
  color: var(--text-secondary);
}

.success-head-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.success-switch {
  display: flex;
  background: #f3f5f3;
  border-radius: var(--radius-sm, 6px);
  padding: 2px;
}

.success-switch button {
  border: 0;
  background: transparent;
  border-radius: var(--radius-sm, 6px);
  padding: 5px 12px;
  font-size: 12.5px;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s ease;
}

.success-switch button.on {
  background: var(--surface);
  color: var(--text);
  font-weight: 700;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
}

.success-search-bar {
  padding: 10px 0 6px;
  flex-shrink: 0;
}

.success-search-input {
  width: 100%;
  box-sizing: border-box;
  padding: 8px 12px;
  border: 1px solid var(--border, rgba(0, 0, 0, 0.1));
  border-radius: var(--radius-sm, 6px);
  background: #f9fafb;
  font-size: 13px;
  outline: none;
  transition: all 0.15s ease;
}

.success-search-input:focus {
  background: #ffffff;
  border-color: var(--accent, #28995a);
}

.success-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  margin: 6px 0;
  border: 1px solid var(--border-light, rgba(0, 0, 0, 0.06));
  border-radius: var(--radius-md, 10px);
}

.success-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  text-align: left;
}

.success-table thead {
  position: sticky;
  top: 0;
  background: #f9fafb;
  z-index: 2;
}

.success-table th {
  padding: 10px 12px;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
  border-bottom: 1px solid var(--border, rgba(0, 0, 0, 0.08));
}

.success-table td {
  padding: 11px 12px;
  border-bottom: 1px solid var(--border-light, rgba(0, 0, 0, 0.04));
  color: var(--text);
}

.success-table tr:hover td {
  background: #f9fafb;
}

.col-file {
  max-width: 260px;
}

.file-name {
  font-weight: 600;
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.part-chip {
  display: inline-block;
  margin-top: 2px;
  padding: 2px 7px;
  border-radius: var(--radius-sm, 6px);
  background: rgba(40, 153, 90, 0.1);
  color: var(--accent, #28995a);
  font-size: 12px;
  font-weight: 500;
}

.col-folder {
  color: var(--text-secondary);
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.status-pill {
  display: inline-block;
  padding: 3px 8px;
  border-radius: var(--radius-sm, 6px);
  font-size: 12px;
  font-weight: 600;
}

.status-pill.ok {
  background: rgba(40, 153, 90, 0.1);
  color: var(--accent, #28995a);
}

.success-empty {
  padding: 32px 16px;
  text-align: center;
  color: var(--text-secondary);
  font-size: 13.5px;
}

.success-pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 10px;
  border-top: 1px solid var(--border-light, rgba(0, 0, 0, 0.06));
  flex-shrink: 0;
  font-size: 12.5px;
  color: var(--text-secondary);
}

.pager-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pager-actions button {
  border: 1px solid var(--border, rgba(0, 0, 0, 0.1));
  background: #ffffff;
  border-radius: var(--radius-sm, 6px);
  padding: 4px 12px;
  font-size: 12.5px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.pager-actions button:hover:not(:disabled) {
  background: #f3f4f6;
  color: var(--text);
}

.pager-actions button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.page-current {
  font-weight: 700;
  color: var(--text);
  padding: 0 4px;
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

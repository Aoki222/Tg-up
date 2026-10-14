/**
 * 左侧监听目录：根开关、子目录阻断、文件树刷新。快捷键留在页面上。
 */
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  deleteRoute,
  fetchFsNodes,
  fetchSettings,
  saveSettings,
  toggleGlobalWatch,
  toggleRootPath,
  toggleRoutePath,
} from "../api";
import type { FolderRouteItem, FsNode, ObserverPathInfo, ObserverRootItem } from "../types";
import { pausedObserverRoot } from "../watchPath";
import { t } from "../i18n";

export function useWatchDock() {
  const watchPaths = ref<string[]>([]);
  const pathInfos = ref<ObserverPathInfo[]>([]);
  const routes = ref<FolderRouteItem[]>([]);
  const watchEnabled = ref(true);
  const observerRoots = ref<ObserverRootItem[]>([]);
  const togglingGlobalWatch = ref(false);
  const togglingRoots = ref<Set<string>>(new Set());
  const togglingRoutes = ref<Set<string>>(new Set());
  const expandedPaths = ref<Set<string>>(new Set());
  const treeEpoch = ref(0);
  const treeRefsMap = new Map<string, unknown>();

  function bumpTrees(): void {
    treeEpoch.value += 1;
  }

  function setTreeRef(path: string, el: unknown): void {
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

  async function loadCardFsNodes(rootPath: string, node: { level: number; data: FsNode }, resolve: (data: FsNode[]) => void): Promise<void> {
    const targetPath = node.level === 0 ? rootPath : node.data.path;
    try {
      const items = await fetchFsNodes(targetPath);
      resolve(items);
    } catch {
      resolve([]);
    }
  }

  async function refreshCardRoot(_rootPath: string): Promise<void> {
    bumpTrees();
    ElMessage.success(t("dock.refreshed"));
  }

  async function refreshCardFsNode(_node: unknown, _data: FsNode): Promise<void> {
    bumpTrees();
  }

  async function restoreSubfolderInheritance(subPath: string): Promise<void> {
    try {
      await deleteRoute(subPath);
      await loadWatchPaths();
      bumpTrees();
      ElMessage.success(t("dock.restored"));
    } catch (err) {
      ElMessage.error(err instanceof Error ? err.message : t("dock.restoreFailed"));
    }
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
    return dockItems.value.filter((item) => item.enabled && item.ok).length;
  });

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
      watchPaths.value = observerRoots.value.map((item) => item.path);
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
        ElMessage.success(t("dock.globalOnToast"));
      } else {
        ElMessage.warning(t("dock.globalOffToast"));
      }
      await loadWatchPaths();
      bumpTrees();
    } catch (err) {
      ElMessage.error(err instanceof Error ? err.message : t("dock.globalFailed"));
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
      const root = res.observer_roots.find((item) => samePath(item.path, path));
      const isNowOn = root ? root.enabled !== false : true;
      if (isNowOn) {
        ElMessage.success(t("dock.rootOn", { name: pathName(path) }));
      } else {
        ElMessage.info(t("dock.rootOff", { name: pathName(path) }));
      }
      bumpTrees();
    } catch (err) {
      ElMessage.error(err instanceof Error ? err.message : t("dock.rootFailed"));
    } finally {
      togglingRoots.value.delete(key);
    }
  }

  function isSubfolderEnabled(data: FsNode): boolean {
    const rule = routes.value.find((item) => samePath(item.path, data.path));
    if (rule) {
      return rule.enabled !== false;
    }
    if (data.current_route && typeof data.current_route.enabled === "boolean") {
      return data.current_route.enabled;
    }
    return true;
  }

  async function handleToggleSubfolder(path: string, _node?: unknown, data?: FsNode): Promise<void> {
    const key = normPath(path);
    if (togglingRoutes.value.has(key)) return;
    togglingRoutes.value.add(key);
    try {
      const res = await toggleRoutePath(path);
      routes.value = res.routes;
      const rule = res.routes.find((item) => samePath(item.path, path));
      const isNowOn = rule ? rule.enabled !== false : true;
      if (isNowOn) {
        ElMessage.success(t("dock.childOn", { name: pathName(path) }));
      } else {
        ElMessage.warning(t("dock.childOff", { name: pathName(path) }));
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
      bumpTrees();
    } catch (err) {
      ElMessage.error(err instanceof Error ? err.message : t("dock.childFailed"));
    } finally {
      togglingRoutes.value.delete(key);
    }
  }

  async function promptAddWatchPath(): Promise<void> {
    try {
      const { value } = await ElMessageBox.prompt(
        t("dock.addBody"),
        t("dock.addTitle"),
        {
          confirmButtonText: t("common.add"),
          cancelButtonText: t("common.cancel"),
          inputPlaceholder: t("dock.addPlaceholder"),
          inputValidator: (val) => {
            if (!val || !val.trim()) return t("dock.pathRequired");
            if (observerRoots.value.some((item) => samePath(item.path, val.trim()))) return t("dock.pathExists");
            return true;
          },
        },
      );
      if (value && value.trim()) {
        const trimmed = value.trim();
        const config = await fetchSettings();
        const roots = [...(config.observer_roots || [])];
        if (!roots.some((item) => samePath(item.path, trimmed))) {
          roots.push(pausedObserverRoot(trimmed));
          config.observer_roots = roots;
          if (config.observer_paths) {
            config.observer_paths = roots.map((item) => item.path);
          }
          await saveSettings(config);
          await loadWatchPaths();
          bumpTrees();
          ElMessage.success(t("dock.added", { name: pathName(trimmed) }));
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
        t("dock.removeBody", { name, path }),
        t("dock.removeTitle"),
        {
          confirmButtonText: t("workers.confirmDelete"),
          cancelButtonText: t("common.cancel"),
          type: "warning",
          confirmButtonClass: "el-button--danger",
          distinguishCancelAndClose: true,
        },
      );
      const config = await fetchSettings();
      config.observer_roots = (config.observer_roots || []).filter((item) => !samePath(item.path, path));
      if (config.observer_paths) {
        config.observer_paths = config.observer_roots.map((item) => item.path);
      }
      const remainingActive = (config.observer_roots || []).filter((item) => item.enabled !== false).length;
      if (remainingActive === 0) {
        config.watch_enabled = false;
      }
      await saveSettings(config);
      expandedPaths.value.delete(normPath(path));
      await loadWatchPaths();
      bumpTrees();
      ElMessage.success(t("dock.removed", { name }));
    } catch {
      // 用户取消删除
    }
  }

  function pathName(path: string): string {
    const parts = path.replace(/\\/g, "/").split("/").filter(Boolean);
    return parts[parts.length - 1] || path;
  }

  onMounted(() => {
    void loadWatchPaths();
  });

  return {
    watchEnabled,
    togglingGlobalWatch,
    togglingRoots,
    togglingRoutes,
    expandedPaths,
    treeEpoch,
    dockItems,
    activeRootsCount,
    bumpTrees,
    setTreeRef,
    isCardExpanded,
    toggleCardExpand,
    loadCardFsNodes,
    refreshCardRoot,
    refreshCardFsNode,
    restoreSubfolderInheritance,
    loadWatchPaths,
    handleToggleGlobalWatch,
    handleToggleRoot,
    isSubfolderEnabled,
    handleToggleSubfolder,
    promptAddWatchPath,
    removeWatchPath,
    normPath,
  };
}

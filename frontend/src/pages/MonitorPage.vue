<script setup lang="ts">
/**
 * 工作台：顶栏固定，左侧监听目录，右侧多维任务调度看板。设置和传输历史记录均在本页模态弹层中。
 * 左右绝对等高、各自独立局部滑动，无整页滚动。
 */

import { computed, nextTick, onDeactivated, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessageBox } from "element-plus";
import WorkerPanel from "../components/WorkerPanel.vue";
import TaskBoard from "../components/TaskBoard.vue";
import SessionPanel from "../components/SessionPanel.vue";
import DirectoryPolicyModal from "../components/DirectoryPolicyModal.vue";
import ManualDispatchModal from "../components/ManualDispatchModal.vue";
import SettingsPage from "./SettingsPage.vue";
import WorkHeader from "../components/WorkHeader.vue";
import WatchDock from "../components/WatchDock.vue";
import { authRequired, fetchSystemVersion, fetchWorkers } from "../api";
import { useTaskBoard } from "../composables/useTaskBoard";
import { useSuccessHistory } from "../composables/useSuccessHistory";
import { useTelegramIdentity } from "../composables/useTelegramIdentity";
import { useUnmatchedPolling } from "../composables/useUnmatched";
import { formatBytes, formatSpeed } from "../format";
import { t } from "../i18n";
import type { ManualDispatchResult, SystemVersionInfo, WorkerSnapshot } from "../types";

defineOptions({ name: "MonitorPage" });

const showSessionForm = ref(false);
const manualDispatchOpen = ref(false);
const route = useRoute();
const router = useRouter();
const { configured, refresh: refreshIdentity } = useTelegramIdentity();
const dockMedia = window.matchMedia("(max-width: 860px)");
const dockOpen = ref(!dockMedia.matches);

// ── 目录树自由拖拽拉伸 ──────────────────────────────────────────
const DOCK_WIDTH_KEY = "uploader_dock_width";
const DEFAULT_DOCK_WIDTH = 340;
const MIN_DOCK_WIDTH = 260;
const MAX_DOCK_WIDTH = 700;

function getStoredDockWidth(): number {
  try {
    const saved = localStorage.getItem(DOCK_WIDTH_KEY);
    if (saved) {
      const val = parseInt(saved, 10);
      if (!isNaN(val) && val >= MIN_DOCK_WIDTH && val <= MAX_DOCK_WIDTH) {
        return val;
      }
    }
  } catch {
    // 忽略异常
  }
  return DEFAULT_DOCK_WIDTH;
}

const dockWidth = ref(getStoredDockWidth());
const isResizing = ref(false);

function onResizeStart(e: MouseEvent): void {
  e.preventDefault();
  isResizing.value = true;
  document.body.style.userSelect = "none";
  document.body.style.cursor = "col-resize";

  const startX = e.clientX;
  const startWidth = dockWidth.value;

  function onMouseMove(moveEvent: MouseEvent): void {
    const deltaX = moveEvent.clientX - startX;
    const maxAllowed = Math.min(MAX_DOCK_WIDTH, Math.floor(window.innerWidth * 0.65));
    const newWidth = Math.min(Math.max(startWidth + deltaX, MIN_DOCK_WIDTH), maxAllowed);
    dockWidth.value = newWidth;
  }

  function onMouseUp(): void {
    isResizing.value = false;
    document.body.style.userSelect = "";
    document.body.style.cursor = "";
    window.removeEventListener("mousemove", onMouseMove);
    window.removeEventListener("mouseup", onMouseUp);
    try {
      localStorage.setItem(DOCK_WIDTH_KEY, String(dockWidth.value));
    } catch {
      // 忽略存储失败
    }
  }

  window.addEventListener("mousemove", onMouseMove);
  window.addEventListener("mouseup", onMouseUp);
}

const settingsOpen = ref(false);
const settingsDirty = ref(false);
const workerModalOpen = ref(false);
const policyModalOpen = ref(false);
const policyPath = ref("");
const settingsRef = ref<InstanceType<typeof SettingsPage> | null>(null);
const watchDockRef = ref<InstanceType<typeof WatchDock> | null>(null);
const tomlSaving = computed(() => settingsRef.value?.saving ?? false);
const versionInfo = ref<SystemVersionInfo | null>(null);
const workers = ref<WorkerSnapshot[]>([]);
let workerTimer = 0;

async function refreshWorkers(): Promise<void> {
  if (authRequired.value) return;
  try {
    workers.value = await fetchWorkers();
  } catch {
    // 忽略异常
  }
}

const {
  items: boardItems,
  successToday,
  failedPage,
  oversizedPage,
  failedTotal,
  oversizedTotal,
  pageSize: boardPageSize,
  setFailedPage,
  setOversizedPage,
} = useTaskBoard();

const { files: unmatchedFiles } = useUnmatchedPolling();
const unmatchedCount = computed(() => unmatchedFiles.value.length);

const {
  successScope,
  successCount,
  successAllTotal,
  successOpen,
  successPage,
  successPages,
  successTotalRows,
  successLoading,
  successSearchText,
  filteredSuccessItems,
  onSuccessScopeChange,
  toggleSuccessScope,
  openSuccess,
  closeSuccess,
  shiftSuccessPage,
} = useSuccessHistory(successToday);

function applyDock(): void {
  dockOpen.value = !dockMedia.matches;
}

dockMedia.addEventListener("change", applyDock);

onMounted(() => {
  window.addEventListener("keydown", onKeydown);
  void refreshIdentity();
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

const liveSpeed = computed(() => {
  const activeTasks = boardItems.value.filter(
    (item) => (item.status === "uploading" || item.status === "assigned") && item.stage !== "success",
  );
  if (activeTasks.length === 0) return "—";
  const totalSpeed = activeTasks.reduce((sum, item) => sum + (item.speed_bps || 0), 0);
  if (totalSpeed > 0) return formatSpeed(totalSpeed);
  return t("version.measuring");
});

const versionLabel = computed(() => {
  const info = versionInfo.value;
  if (!info) return t("version.latestFallback");
  const rev = shortRevision(info.current_version);
  if (info.status === "update") return t("version.update");
  if (info.status === "dev") return t("version.dev");
  if (info.status === "staging") return rev ? t("version.stagingRev", { rev }) : t("version.staging");
  return rev ? t("version.rev", { rev }) : (info.current_version || t("version.latest"));
});

const versionTitle = computed(() => versionInfo.value?.commit_message || t("version.click"));

function onVersionClick(): void {
  const info = versionInfo.value;
  if (!info) {
    ElMessageBox.alert(t("version.healthy"), t("version.title"), {
      confirmButtonText: t("common.ok"),
      type: "info",
    });
    return;
  }
  const rev = shortRevision(info.current_version);
  const statusMsg =
    info.status === "update"
      ? t("version.updateBody")
      : info.status === "staging"
      ? t("version.stagingBody")
      : t("version.currentBody");
  const commitMsg = info.commit_message ? `\n${t("version.commit", { message: info.commit_message })}` : "";
  ElMessageBox.alert(
    t("version.summary", {
      version: info.current_version,
      rev: rev ? `rev ${rev}` : info.status,
      status: statusMsg,
      commit: commitMsg,
    }),
    t("version.title"),
    {
      confirmButtonText: t("common.ok"),
      type: info.status === "update" ? "warning" : "info",
    },
  );
}

function shortRevision(value: string): string {
  const text = value.trim();
  if (!text || text === "dev" || text === "local_dev") return "";
  return text.length > 7 ? text.slice(0, 7) : text;
}

async function reloadWatch(refreshTrees: boolean): Promise<void> {
  await watchDockRef.value?.loadWatchPaths();
  if (refreshTrees) watchDockRef.value?.bumpTrees();
}

async function handleSaveToml(): Promise<void> {
  await settingsRef.value?.submitToml();
  await reloadWatch(true);
}

async function onPolicySaved(): Promise<void> {
  await reloadWatch(true);
}

async function onManualDispatched(_res: ManualDispatchResult): Promise<void> {
  await reloadWatch(true);
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
    void reloadWatch(false);
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
  workers.value.filter((worker) => worker.enabled && worker.running && worker.accepting).length,
);

const totalWorkersCount = computed(() => workers.value.length);

function onKeydown(event: KeyboardEvent): void {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "b") {
    event.preventDefault();
    dockOpen.value = !dockOpen.value;
  } else if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "h") {
    event.preventDefault();
    openSuccess();
  } else if ((event.metaKey || event.ctrlKey) && event.key === ",") {
    event.preventDefault();
    openSettings("telegram");
  } else if (event.key === "Escape") {
    if (policyModalOpen.value) policyModalOpen.value = false;
    else if (workerModalOpen.value) workerModalOpen.value = false;
    else if (successOpen.value) closeSuccess();
    else if (settingsOpen.value) closeSettings();
    else if (showSessionForm.value) closeSessionForm();
  }
}

watch(
  [showSessionForm, successOpen, settingsOpen, workerModalOpen, policyModalOpen],
  ([sessionOpen, historyOpen, settings, workerOpen, policyOpen]) => {
    document.body.style.overflow = sessionOpen || historyOpen || settings || workerOpen || policyOpen ? "hidden" : "";
  },
);

watch(
  () => String(route.query.settings || ""),
  (requested) => {
    if (!requested) {
      const wasOpen = settingsOpen.value;
      settingsOpen.value = false;
      if (wasOpen) void reloadWatch(false);
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
    <WorkHeader
      :dock-open="dockOpen"
      :version-label="versionLabel"
      :version-info="versionInfo"
      :version-title="versionTitle"
      :live-speed="liveSpeed"
      :active-workers="activeWorkersCount"
      :total-workers="totalWorkersCount"
      :success-scope="successScope"
      :success-count="successCount"
      :success-all-total="successAllTotal"
      :settings-dirty="settingsDirty"
      @toggle-dock="dockOpen = !dockOpen"
      @open-version="onVersionClick"
      @open-workers="workerModalOpen = true"
      @open-success="openSuccess"
      @toggle-scope="toggleSuccessScope()"
      @open-settings="openSettings('telegram')"
    />

    <p v-if="authRequired" class="setup-banner auth-banner">
      <span>{{ t("banner.auth") }}</span>
      <button type="button" @click="openSettings('account')">{{ t("banner.authAction") }}</button>
    </p>
    <p v-else-if="configured === false" class="setup-banner">
      {{ t("banner.credentials") }}
      <button type="button" @click="openSettings('account')">{{ t("banner.credentialsAction") }}</button>
    </p>
    <p v-if="unmatchedCount > 0" class="unmatched-banner">
      {{ t("banner.unmatched", { n: unmatchedCount }) }}
    </p>

    <div class="work-body">
      <button
        v-if="dockOpen"
        type="button"
        class="dock-scrim"
        :aria-label="t('banner.closeDock')"
        @click="dockOpen = false"
      ></button>

      <WatchDock
        ref="watchDockRef"
        :open="dockOpen"
        :width="dockWidth"
        :is-resizing="isResizing"
        @close="dockOpen = false"
        @open-policy="openPolicy"
        @open-manual="manualDispatchOpen = true"
      />

      <div
        v-if="dockOpen"
        class="dock-resizer"
        :class="{ 'is-dragging': isResizing }"
        :title="t('banner.resizeDock')"
        @mousedown="onResizeStart"
      >
        <div class="dock-resizer-line"></div>
      </div>

      <TaskBoard
        class="board-pane"
        :items="boardItems"
        :failed-page="failedPage"
        :oversized-page="oversizedPage"
        :failed-total="failedTotal"
        :oversized-total="oversizedTotal"
        :page-size="boardPageSize"
        @change-failed-page="setFailedPage"
        @change-oversized-page="setOversizedPage"
      />
    </div>

    <!-- ══════════════════════════════════════════════════════════
         系统配置模态弹窗 (In-Place Modal)
    ══════════════════════════════════════════════════════════ -->
    <div v-show="settingsOpen" class="settings-layer" @click.self="closeSettings">
      <div class="settings-modal" role="dialog" :aria-label="t('settings.dialog')" @click.stop>
        <header class="settings-head">
          <div class="settings-head-left">
            <h2>{{ t("settings.heading") }}</h2>
            <span v-if="settingsDirty" class="unsaved-badge">{{ t("settings.unsaved") }}</span>
          </div>
          <div class="settings-head-right">
            <button
              type="button"
              class="save-toml-btn"
              :disabled="!settingsDirty || tomlSaving"
              :title="t('settings.saveTitle')"
              @click="handleSaveToml"
            >
              <span v-if="tomlSaving" class="btn-spinner"></span>
              <span>{{ t("settings.save") }}</span>
            </button>
            <button type="button" class="modal-close-btn" :title="t('settings.close')" @click="closeSettings">✕</button>
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
        <div class="session-modal success-modal" role="dialog" :aria-label="t('history.dialog')" @click.stop>
          <header class="success-head">
            <div class="success-head-left">
              <span class="success-badge-dot"></span>
              <div>
                <h2>{{ t("history.heading") }}</h2>
                <p class="success-sub">
                  {{ t("history.sub", { scope: successScope === "today" ? t("history.today") : t("history.all"), n: successTotalRows }) }}
                </p>
              </div>
            </div>
            <div class="success-head-right">
              <div class="success-switch">
                <button type="button" :class="{ on: successScope === 'today' }" @click="onSuccessScopeChange('today')">{{ t("history.todayBtn") }}</button>
                <button type="button" :class="{ on: successScope === 'all' }" @click="onSuccessScopeChange('all')">{{ t("history.allBtn") }}</button>
              </div>
              <button type="button" class="modal-close-btn" :title="t('history.close')" @click="closeSuccess">✕</button>
            </div>
          </header>

          <div class="success-search-bar">
            <input
              v-model="successSearchText"
              type="text"
              :placeholder="t('history.search')"
              class="success-search-input"
            />
          </div>

          <div class="success-body">
            <p v-if="successLoading" class="success-empty">{{ t("history.loading") }}</p>
            <p v-else-if="filteredSuccessItems.length === 0" class="success-empty">{{ t("history.empty") }}</p>
            <table v-else class="success-table">
              <thead>
                <tr>
                  <th>{{ t("history.file") }}</th>
                  <th>{{ t("history.source") }}</th>
                  <th>{{ t("history.size") }}</th>
                  <th>{{ t("history.status") }}</th>
                  <th>{{ t("history.finished") }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in filteredSuccessItems" :key="item.id">
                  <td class="col-file" :title="item.file_name">
                    <span class="file-name">{{ item.file_name }}</span>
                    <span v-if="item.part_label" class="part-chip">{{ item.part_label }}</span>
                  </td>
                  <td class="col-folder font-mono" :title="item.folder_name || t('history.root')">{{ item.folder_name || "—" }}</td>
                  <td class="col-size font-mono">{{ item.file_size > 0 ? formatBytes(item.file_size) : "—" }}</td>
                  <td class="col-status"><span class="status-pill ok">{{ t("history.delivered") }}</span></td>
                  <td class="col-time font-mono">{{ item.finished_at || "—" }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <footer class="success-pager">
            <span class="pager-stat">{{ t("history.pager", { page: successPage, pages: successPages, total: successTotalRows }) }}</span>
            <div class="pager-actions">
              <button type="button" :disabled="successPage <= 1 || successLoading" @click="shiftSuccessPage(-1)">{{ t("history.prev") }}</button>
              <span class="page-current font-mono">{{ successPage }}</span>
              <button type="button" :disabled="successPage >= successPages || successLoading" @click="shiftSuccessPage(1)">{{ t("history.next") }}</button>
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
        <div class="worker-modal panel-card" role="dialog" :aria-label="t('workers.dialog')" @click.stop>
          <header class="worker-modal-head">
            <div class="worker-modal-head-left">
              <span class="worker-badge-dot"></span>
              <div>
                <h2>{{ t("workers.heading") }}</h2>
                <p class="worker-sub">{{ t("workers.sub", { active: activeWorkersCount, total: totalWorkersCount }) }}</p>
              </div>
            </div>
            <div class="worker-modal-head-right">
              <button type="button" class="add-session-btn" @click="openSessionForm">
                {{ t("workers.add") }}
              </button>
              <button type="button" class="modal-close-btn" :title="t('workers.close')" @click="workerModalOpen = false">✕</button>
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

<style scoped src="./MonitorPage.css"></style>


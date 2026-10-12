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
import { fetchSystemVersion, fetchWorkers } from "../api";
import { useTaskBoard } from "../composables/useTaskBoard";
import { useSuccessHistory } from "../composables/useSuccessHistory";
import { useTelegramIdentity } from "../composables/useTelegramIdentity";
import { useUnmatchedPolling } from "../composables/useUnmatched";
import { formatBytes, formatSpeed } from "../format";
import type { ManualDispatchResult, SystemVersionInfo, WorkerSnapshot } from "../types";

defineOptions({ name: "MonitorPage" });

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
const settingsRef = ref<InstanceType<typeof SettingsPage> | null>(null);
const watchDockRef = ref<InstanceType<typeof WatchDock> | null>(null);
const tomlSaving = computed(() => settingsRef.value?.saving ?? false);
const versionInfo = ref<SystemVersionInfo | null>(null);
const workers = ref<WorkerSnapshot[]>([]);
let workerTimer = 0;

async function refreshWorkers(): Promise<void> {
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

      <WatchDock
        ref="watchDockRef"
        :open="dockOpen"
        @close="dockOpen = false"
        @open-policy="openPolicy"
        @open-manual="manualDispatchOpen = true"
      />

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

<style scoped src="./MonitorPage.css"></style>


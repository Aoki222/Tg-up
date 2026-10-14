<script setup lang="ts">
/**
 * @file ConfigPanel.vue
 * @description 上传参数热更配置面板 (Config Panel)
 *
 * 核心设计：
 * 1. 【upload.toml 热更新控制】：对应服务端的配置文件，保存后由后端的 SettingsHub 自动热加载生效；
 * 2. 【脏值校验机制 (Dirty Checking)】：
 *    - 维护 `savedSnapshot` 序列化快照（路径与格式数组排序后对比）；
 *    - 仅在用户切实修改了表单值时，才激活「保存到 upload.toml」按钮，防止无效提交；
 * 3. 【多监听目录健康诊断】：展示各个监听目录的存在性与读取权限状态。
 */

import { computed, onMounted, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  createChatTopic,
  deleteChatTopic,
  fetchChatTopics,
  fetchDialogChats,
  fetchSettings,
  renameChatTopic,
  saveSettings,
  syncDialogChats,
} from "../api";
import type { DialogChat, ForumTopic } from "../api";
import type { UploadConfig } from "../types";
import { pausedObserverRoot } from "../watchPath";
import { ENABLE_GOOGLE_DRIVE } from "../features";
import { t } from "../i18n";

const { panel } = defineProps<{
  panel: "routes" | "telegram" | "drive" | "watch" | "process";
}>();

const emit = defineEmits<{
  "update:dirty": [value: boolean];
}>();

// ── 响应式状态 ─────────────────────────────────────────────────

/** 数据加载中的 loading 遮罩 */
const loading = ref(false);

/** 保存中防止重复点击的 loading 状态 */
const saving = ref(false);

/** 上次持久化成功的配置数据快照字符串（用于脏检查） */
const savedSnapshot = ref("");

/** 表单绑定的配置实体数据 */
const form = reactive<UploadConfig>({
  chat_id: 0,
  observer_roots: [],
  observer_paths: [],
  observer_path_infos: [],
  page_dir: "page",
  archive_dir: "uploaded",
  preview: "first_frame",
  grid_layout: "3x5",
  grid_timestamp: true,
  grid_metadata: true,
  caption_template: "",
  topic_creation_enabled: true,
  after_success: "keep",
  auto_slice: false,
  schedule_order: "oldest",
  concurrency: 1,
  max_retries: 3,
  upload_timeout_seconds: 1200,
  assigned_timeout_seconds: 600,
  stable_timeout_seconds: 1800,
  watch_extensions: ["mp4", "mkv", "avi", "mov", "wmv", "m4v"],
  routes: [],
  chats: [],
  drive_folders: [],
});

const scheduleOrderHint = computed(() => {
  if (form.schedule_order === "natural") return t("config.order.natural");
  if (form.schedule_order === "created") return t("config.order.created");
  if (form.schedule_order === "smallest") return t("config.order.smallest");
  return t("config.order.oldest");
});

const globalGridParsed = computed(() => {
  const layout = form.grid_layout;
  if (!layout || !layout.trim()) {
    return { valid: false, text: t("config.gridEmpty") };
  }
  const clean = layout.toLowerCase().replace(/[*×,]/g, "x").trim();
  const parts = clean.split("x");
  if (parts.length === 2) {
    const c = parseInt(parts[0].trim(), 10);
    const r = parseInt(parts[1].trim(), 10);
    if (!isNaN(c) && !isNaN(r) && c >= 1 && c <= 20 && r >= 1 && r <= 20) {
      return {
        valid: true,
        text: t("config.gridOk", { cols: c, rows: r, frames: c * r }),
      };
    }
  }
  return {
    valid: false,
    text: t("config.gridBad"),
  };
});

// ── 脏检查机制 ─────────────────────────────────────────────────

/**
 * 将配置对象序列化为规格化的 JSON 字符串（数组预先 trim 并排序，消除乱序干扰）
 */
function snapshotOf(config: UploadConfig): string {
  return JSON.stringify({
    ...config,
    observer_roots: [...(config.observer_roots ?? [])]
      .map((item) => ({ path: item.path.trim(), enabled: item.enabled }))
      .sort((left, right) => left.path.localeCompare(right.path)),
    watch_extensions: [...config.watch_extensions].map((item) => item.trim()).sort(),
    routes: [...(config.routes ?? [])]
      .map((item) => ({
        name: (item.name || "").trim(),
        path: (item.path || "").trim(),
        chat_id: item.chat_id,
        topic_enabled: item.topic_enabled,
        caption_template: item.caption_template ?? null,
        preview: item.preview ?? null,
        grid_layout: item.grid_layout ?? null,
        topic_mode: item.topic_mode ?? null,
        topic_id: item.topic_id ?? null,
        enabled: item.enabled !== false,
        platform: item.platform || "telegram",
        dest_id: item.dest_id || (item.chat_id ? String(item.chat_id) : ""),
      }))
      .sort((left, right) => left.path.localeCompare(right.path)),
    chats: [...(config.chats ?? [])]
      .map((item) => ({
        chat_id: item.chat_id,
        alias: item.alias.trim(),
        title: item.title?.trim() ?? "",
      }))
      .sort((left, right) => left.chat_id - right.chat_id),
    drive_folders: [...(config.drive_folders ?? [])]
      .map((item) => ({ folder_id: item.folder_id.trim(), name: item.name.trim() }))
      .sort((left, right) => left.folder_id.localeCompare(right.folder_id)),
  });
}

/**
 * 将服务端返回的配置合并入本地表单，并重置脏检查基准快照
 */
function applyServer(data: UploadConfig): void {
  Object.assign(form, data, {
    observer_roots: data.observer_roots ?? [],
    routes: data.routes ?? [],
    chats: data.chats ?? [],
    drive_folders: data.drive_folders ?? [],
  });
  savedSnapshot.value = snapshotOf({ ...form });
}

const showAddChat = ref(false);
const addingChat = ref(false);
const dialogChats = ref<DialogChat[]>([]);
const dialogReason = ref("");
const chatQuery = ref("");
const stylePath = ref("");
const topicChatId = ref<number | null>(null);
const topicItems = ref<ForumTopic[]>([]);
const topicTitle = ref("");
const topicRenameId = ref<number | null>(null);
const topicRenameTitle = ref("");
const topicLoading = ref(false);
const topicForum = ref(true);
const topicReason = ref("");

function samePath(left: string, right: string): boolean {
  // 统一斜杠、去掉末尾分隔符并忽略大小写，用于匹配目录路由。
  return left.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase() === right.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}



function openRouteSettings(path: string): void {
  stylePath.value = path;
}

async function openTopicManager(chatId: number): Promise<void> {
  topicChatId.value = chatId;
  topicTitle.value = "";
  topicRenameId.value = null;
  await reloadTopics(false);
}

async function reloadTopics(refresh: boolean): Promise<void> {
  if (topicChatId.value == null) return;
  topicLoading.value = true;
  try {
    const data = await fetchChatTopics(topicChatId.value, refresh);
    topicForum.value = data.forum;
    topicReason.value = data.reason;
    topicItems.value = data.topics;
  } catch (error) {
    topicItems.value = [];
    topicForum.value = false;
    topicReason.value = error instanceof Error ? error.message : t("config.topicReadFailed");
  } finally {
    topicLoading.value = false;
  }
}

async function onCreateTopic(): Promise<void> {
  if (topicChatId.value == null || !topicTitle.value.trim()) return;
  try {
    await createChatTopic(topicChatId.value, topicTitle.value.trim());
    topicTitle.value = "";
    await reloadTopics(false);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t("config.topicCreateFailed"));
  }
}

async function onRenameTopic(topicId: number): Promise<void> {
  if (topicChatId.value == null || !topicRenameTitle.value.trim()) return;
  try {
    await renameChatTopic(topicChatId.value, topicId, topicRenameTitle.value.trim());
    topicRenameId.value = null;
    await reloadTopics(false);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t("config.topicRenameFailed"));
  }
}

async function onDeleteTopic(topicId: number): Promise<void> {
  if (topicChatId.value == null) return;
  try {
    await deleteChatTopic(topicChatId.value, topicId);
    await reloadTopics(false);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t("config.topicDeleteFailed"));
  }
}



const showAddDrive = ref(false);
const newDriveName = ref("");
const newDriveId = ref("");

function confirmAddDrive(): void {
  // 校验并追加 Drive 文件夹，供目录路由选择器使用。
  const folderId = newDriveId.value.trim();
  if (!folderId) {
    ElMessage.error(t("config.driveId"));
    return;
  }
  if (form.drive_folders.some((item) => item.folder_id === folderId)) {
    ElMessage.error(t("config.driveExists"));
    return;
  }
  form.drive_folders.push({ folder_id: folderId, name: newDriveName.value.trim() });
  showAddDrive.value = false;
}

function removeDrive(folderId: string): void {
  // 删除 Drive 文件夹，并同步删除引用它的目录路由。
  form.drive_folders = form.drive_folders.filter((item) => item.folder_id !== folderId);
  form.routes = form.routes.filter((item) => !(item.platform === "gdrive" && item.dest_id === folderId));
}

const filteredDialogs = computed(() => {
  const query = chatQuery.value.trim().toLowerCase();
  if (!query) return dialogChats.value;
  return dialogChats.value.filter((item) => item.title.toLowerCase().includes(query) || String(item.id).includes(query));
});

async function openAddChat(): Promise<void> {
  // 打开群组选择器并从后端读取当前个人账号可见的群组。
  showAddChat.value = true;
  chatQuery.value = "";
  addingChat.value = true;
  dialogReason.value = "";
  try {
    const data = await fetchDialogChats();
    dialogChats.value = data.items;
    dialogReason.value = data.online ? "" : data.reason || t("config.dialogsOffline");
  } catch (error) {
    dialogChats.value = [];
    dialogReason.value = error instanceof Error ? error.message : t("config.dialogsFailed");
  } finally {
    addingChat.value = false;
  }
}

async function syncChats(): Promise<void> {
  addingChat.value = true;
  dialogReason.value = "";
  try {
    const data = await syncDialogChats();
    dialogChats.value = data.items;
    dialogReason.value = data.online ? "" : data.reason || t("config.dialogsOffline");
  } catch (error) {
    dialogReason.value = error instanceof Error ? error.message : t("config.syncFailed");
  } finally {
    addingChat.value = false;
  }
}

async function persistChatConfig(
  previousChats: typeof form.chats,
  previousRoutes: typeof form.routes,
): Promise<void> {
  saving.value = true;
  try {
    applyServer(await saveSettings({ ...form }));
    ElMessage.success(t("config.chatsSaved"));
  } catch (error) {
    form.chats = previousChats;
    form.routes = previousRoutes;
    ElMessage.error(error instanceof Error ? error.message : t("config.chatsSaveFailed"));
  } finally {
    saving.value = false;
  }
}

async function pickChat(chat: DialogChat): Promise<void> {
  // 将选中的群组加入配置，重复选择直接忽略，并立即写入 upload.toml。
  if (form.chats.some((item) => item.chat_id === chat.id)) return;
  const previousChats = [...form.chats];
  const previousRoutes = [...form.routes];
  form.chats.push({ chat_id: chat.id, alias: "", title: chat.title });
  await persistChatConfig(previousChats, previousRoutes);
}

async function removeChat(chatId: number): Promise<void> {
  // 删除群组别名和相关路由，并立即写入 upload.toml。
  const previousChats = [...form.chats];
  const previousRoutes = [...form.routes];
  form.chats = form.chats.filter((item) => item.chat_id !== chatId);
  form.routes = form.routes.filter((item) => item.chat_id !== chatId);
  await persistChatConfig(previousChats, previousRoutes);
}

/** 当前表单是否有未保存的变更 */
const dirty = computed(() => savedSnapshot.value !== "" && snapshotOf({ ...form }) !== savedSnapshot.value);

watch(dirty, (value) => emit("update:dirty", value), { immediate: true });

// ── 数据加载与保存 ─────────────────────────────────────────────

/** 从服务端获取当前生效的 upload.toml 配置 */
async function load(): Promise<void> {
  loading.value = true;
  try {
    applyServer(await fetchSettings());
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t("config.loadFailed"));
  } finally {
    loading.value = false;
  }
}

/** 提交表单保存配置到 upload.toml */
async function submit(): Promise<void> {
  if (!dirty.value) {
    return;
  }
  saving.value = true;
  try {
    applyServer(await saveSettings({ ...form }));
    ElMessage.success(t("config.saved"));
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t("config.saveFailed"));
  } finally {
    saving.value = false;
  }
}

async function promptAddObserverPath(): Promise<void> {
  try {
    const { value } = await ElMessageBox.prompt(
      t("config.addBody"),
      t("config.addTitle"),
      {
        confirmButtonText: t("common.add"),
        cancelButtonText: t("common.cancel"),
        inputPlaceholder: t("config.addPlaceholder"),
        inputValidator: (val) => {
          if (!val || !val.trim()) return t("config.pathRequired");
          if (form.observer_roots.some((p) => samePath(p.path, val.trim()))) return t("config.pathExists");
          return true;
        },
      },
    );
    if (value && value.trim()) {
      const trimmed = value.trim();
      form.observer_roots.push(pausedObserverRoot(trimmed));
      form.observer_paths = form.observer_roots.map((r) => r.path);
      ElMessage.success(t("config.added"));
    }
  } catch {
    // 用户取消输入
  }
}

function removeObserverPath(path: string): void {
  form.observer_roots = form.observer_roots.filter((p) => !samePath(p.path, path));
  form.observer_paths = form.observer_roots.map((r) => r.path);
}

function getPathStatus(path: string): { ok: boolean; text: string } {
  const root = form.observer_roots.find((item) => samePath(item.path, path));
  if (root) {
    if (!root.enabled) {
      return { ok: false, text: t("config.statusOff") };
    }
    if (root.ok) {
      return { ok: true, text: t("config.statusOk") };
    }
    return {
      ok: false,
      text: t("config.statusBad", { error: root.error || t("config.statusBadFallback") }),
    };
  }
  return { ok: true, text: t("config.statusReady") };
}

const newExtInput = ref("");

function addExtension(): void {
  const ext = newExtInput.value.trim().replace(/^\./, "").toLowerCase();
  if (!ext) return;
  if (!form.watch_extensions.includes(ext)) {
    form.watch_extensions.push(ext);
  }
  newExtInput.value = "";
}

function removeExtension(ext: string): void {
  form.watch_extensions = form.watch_extensions.filter((item) => item !== ext);
}

function hasCaptionChip(token: string): boolean {
  return Boolean(form.caption_template && form.caption_template.includes(token));
}

function toggleCaptionChip(token: string): void {
  const current = form.caption_template || "";
  if (hasCaptionChip(token)) {
    form.caption_template = current
      .replaceAll(token, "")
      .replace(/\s{2,}/g, " ")
      .trim();
  } else {
    form.caption_template = current ? `${current} ${token}` : token;
  }
}

onMounted(() => {
  void load();
});

defineExpose({
  dirty,
  openRouteSettings,
  submit,
  saving,
  form,
  chatsCount: computed(() => form.chats.length),
  pathsCount: computed(() => form.observer_roots.length),
});
</script>

<template>
  <div v-loading="loading" class="config-pane-wrap">
    <!-- ── 1. Telegram 目标 (telegram) ── -->
    <div v-if="panel === 'telegram'" class="panel-section-group">
      <!-- 默认目标群组 / 频道 -->
      <div class="inset-card">
        <div class="card-head">
          <h3 class="card-title">{{ t("config.defaultTitle") }}</h3>
          <p class="card-hint">{{ t("config.defaultHint") }}</p>
        </div>

        <div class="form-field-sm">
          <label class="form-label">{{ t("config.defaultLabel") }}</label>
          <input
            v-model.number="form.chat_id"
            type="number"
            placeholder="-100192837482"
            class="app-input mono max-w-sm"
          />
        </div>
      </div>

      <!-- 已配置的目标列表 (chats) -->
      <div class="inset-card">
        <div class="card-head-row">
          <div>
            <h3 class="card-title">{{ t("config.chatsTitle") }}</h3>
            <p class="card-hint">{{ t("config.chatsHint") }}</p>
          </div>
          <button type="button" class="btn-light-green" @click="openAddChat">
            {{ t("config.addChat") }}
          </button>
        </div>

        <div class="chats-list-wrap">
          <div v-if="form.chats.length === 0" class="empty-list-notice">
            {{ t("config.chatsEmpty") }}
          </div>
          <div
            v-for="item in form.chats"
            :key="item.chat_id"
            class="chat-row-item"
          >
            <div class="chat-row-main">
              <div class="chat-title-line">
                <span class="chat-main-name">{{ item.alias.trim() || item.title.trim() || t("config.unnamed") }}</span>
                <span class="chat-mono-id">({{ item.chat_id }})</span>
              </div>
              <div v-if="item.alias.trim() && item.title.trim()" class="chat-sub-line">
                {{ t("config.chatMeta", { alias: item.alias, title: item.title }) }}
              </div>
            </div>
            <div class="chat-row-actions">
              <button
                type="button"
                class="btn-outline-sm"
                @click="openTopicManager(item.chat_id)"
              >
                {{ t("config.topics") }}
              </button>
              <button
                type="button"
                class="btn-danger-text"
                @click="removeChat(item.chat_id)"
              >
                {{ t("common.delete") }}
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- 全局话题新建开关 -->
      <div class="inset-card">
        <div class="card-switch-row">
          <div>
            <h3 class="card-title">{{ t("config.topicSwitch") }}</h3>
            <p class="card-hint">{{ t("config.topicSwitchHint") }}</p>
          </div>
          <el-switch v-model="form.topic_creation_enabled" />
        </div>
      </div>
    </div>

    <!-- ── 2. 监听目录 (watch) ── -->
    <div v-if="panel === 'watch'" class="panel-section-group">
      <!-- 监控目录列表 -->
      <div class="inset-card">
        <div class="card-head-row">
          <div>
            <h3 class="card-title">{{ t("config.rootsTitle") }}</h3>
            <p class="card-hint">{{ t("config.rootsHint") }}</p>
          </div>
          <button type="button" class="btn-light-green" @click="promptAddObserverPath">
            {{ t("config.addRoot") }}
          </button>
        </div>

        <div class="paths-list-wrap">
          <div v-if="form.observer_roots.length === 0" class="empty-list-notice">
            {{ t("config.rootsEmpty") }}
          </div>
          <div
            v-for="root in form.observer_roots"
            :key="root.path"
            class="path-card-item"
          >
            <div class="path-card-left">
              <span class="path-status-dot" :class="getPathStatus(root.path).ok ? 'ok' : 'err'"></span>
              <div class="path-card-text">
                <div class="path-card-name">{{ root.path }}</div>
                <div class="path-card-sub">{{ getPathStatus(root.path).text }}</div>
              </div>
            </div>
            <div style="display: flex; align-items: center; gap: 12px;">
              <el-switch
                v-model="root.enabled"
                size="small"
                :active-text="t('config.enable')"
                :inactive-text="t('config.pause')"
                inline-prompt
              />
              <button
                type="button"
                class="btn-danger-text"
                @click="removeObserverPath(root.path)"
              >
                {{ t("common.delete") }}
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- 文件格式过滤白名单 -->
      <div class="inset-card">
        <div class="card-head">
          <h3 class="card-title">{{ t("config.extTitle") }}</h3>
          <p class="card-hint">{{ t("config.extHint") }}</p>
        </div>

        <div class="ext-chips-box">
          <span
            v-for="ext in form.watch_extensions"
            :key="ext"
            class="ext-chip"
          >
            {{ ext }}
            <button type="button" class="chip-remove" @click="removeExtension(ext)">✕</button>
          </span>
          <div class="ext-add-row">
            <input
              v-model="newExtInput"
              type="text"
              :placeholder="t('config.extPlaceholder')"
              class="ext-inline-input"
              @keyup.enter="addExtension"
            />
            <button type="button" class="ext-inline-btn" @click="addExtension">{{ t("config.extAdd") }}</button>
          </div>
        </div>
      </div>

      <!-- 文件稳定静默超时 -->
      <div class="inset-card">
        <div class="card-split-row">
          <div>
            <h3 class="card-title">{{ t("config.stableTitle") }}</h3>
            <p class="card-hint">{{ t("config.stableHint") }}</p>
          </div>
          <div class="input-with-unit-box">
            <input
              v-model.number="form.stable_timeout_seconds"
              type="number"
              min="1"
              class="app-input mono w-32"
            />
            <span class="unit-label">{{ t("common.seconds") }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- ── 3. 处理与收尾 (process) ── -->
    <div v-if="panel === 'process'" class="panel-section-group">
      <!-- 视频封面与说明模板 -->
      <div class="inset-card">
        <div class="card-head">
          <h3 class="card-title">{{ t("config.coverTitle") }}</h3>
          <p class="card-hint">{{ t("config.coverHint") }}</p>
        </div>

        <div class="table-list-divide">
          <div class="table-row-item">
            <div class="row-info-col">
              <div class="row-info-title">{{ t("config.previewTitle") }}</div>
              <div class="row-info-sub">{{ t("config.previewSub") }}</div>
            </div>
            <div class="row-ctrl-col w-56">
              <select v-model="form.preview" class="app-select">
                <option value="first_frame">{{ t("config.previewFrame") }}</option>
                <option value="grid">{{ t("config.previewGrid") }}</option>
                <option value="off">{{ t("config.previewOff") }}</option>
              </select>
            </div>
          </div>

          <!-- 当开启自定义网格图时联动展示 -->
          <div v-if="form.preview === 'grid'" class="table-row-item vertical">
            <div class="row-top-flex">
              <div>
                <div class="row-info-title">{{ t("config.gridTitle") }}</div>
                <div class="row-info-sub">
                  {{ t("config.gridSub") }}
                </div>
              </div>
              <div class="grid-status-tip-inline" :class="{ 'is-err': !globalGridParsed.valid }">
                <span class="grid-status-dot"></span>
                <span class="grid-status-text">{{ globalGridParsed.text }}</span>
              </div>
            </div>
            <div class="grid-layout-input-row">
              <input
                v-model="form.grid_layout"
                type="text"
                class="app-input mono grid-spec-input"
                :placeholder="t('config.gridPlaceholder')"
              />
              <div class="token-chips-group">
                <span class="grid-presets-inline-title">{{ t("config.gridPresets") }}</span>
                <button
                  v-for="p in ['3x5', '4x3', '3x3', '4x4', '2x4', '5x2']"
                  :key="p"
                  type="button"
                  class="token-btn"
                  :class="{ active: form.grid_layout?.toLowerCase().trim() === p }"
                  @click="form.grid_layout = p"
                >
                  {{ p }}
                </button>
              </div>
            </div>
          </div>

          <div v-if="form.preview === 'grid'" class="table-row-item">
            <div class="row-info-col">
              <div class="row-info-title">{{ t("config.stampTitle") }}</div>
              <div class="row-info-sub">{{ t("config.stampSub") }}</div>
            </div>
            <div class="row-ctrl-col">
              <el-switch v-model="form.grid_timestamp" />
            </div>
          </div>

          <div v-if="form.preview === 'grid'" class="table-row-item">
            <div class="row-info-col">
              <div class="row-info-title">{{ t("config.metaTitle") }}</div>
              <div class="row-info-sub">{{ t("config.metaSub") }}</div>
            </div>
            <div class="row-ctrl-col">
              <el-switch v-model="form.grid_metadata" />
            </div>
          </div>

          <div class="table-row-item">
            <div class="row-info-col">
              <div class="row-info-title">{{ t("config.pageTitle") }}</div>
              <div class="row-info-sub">{{ t("config.pageSub") }}</div>
            </div>
            <div class="row-ctrl-col w-48">
              <input v-model="form.page_dir" type="text" class="app-input mono" />
            </div>
          </div>

          <div class="table-row-item vertical">
            <div class="row-top-flex">
              <div>
                <div class="row-info-title">{{ t("config.captionTitle") }}</div>
                <div class="row-info-sub">{{ t("config.captionSub") }}</div>
              </div>
              <div class="token-chips-group">
                <button
                  type="button"
                  class="token-btn"
                  :class="{ active: hasCaptionChip('{file_name}') }"
                  @click="toggleCaptionChip('{file_name}')"
                >
                  <span v-if="hasCaptionChip('{file_name}')">✓ </span>{file_name}
                </button>
                <button
                  type="button"
                  class="token-btn"
                  :class="{ active: hasCaptionChip('{size}') }"
                  @click="toggleCaptionChip('{size}')"
                >
                  <span v-if="hasCaptionChip('{size}')">✓ </span>{size}
                </button>
                <button
                  type="button"
                  class="token-btn"
                  :class="{ active: hasCaptionChip('{folder}') }"
                  @click="toggleCaptionChip('{folder}')"
                >
                  <span v-if="hasCaptionChip('{folder}')">✓ </span>{folder}
                </button>
              </div>
            </div>
            <input
              v-model="form.caption_template"
              type="text"
              :placeholder="t('config.captionPlaceholder')"
              class="app-input mono"
            />
          </div>
        </div>
      </div>

      <!-- 上传完成与超限处理 -->
      <div class="inset-card">
        <div class="card-head">
          <h3 class="card-title">{{ t("config.doneTitle") }}</h3>
          <p class="card-hint">{{ t("config.doneHint") }}</p>
        </div>

        <div class="table-list-divide">
          <div class="table-row-item">
            <div class="row-info-col">
              <div class="row-info-title">{{ t("config.afterTitle") }}</div>
              <div class="row-info-sub">{{ t("config.afterSub") }}</div>
            </div>
            <div class="row-ctrl-col w-56">
              <select v-model="form.after_success" class="app-select">
                <option value="keep">{{ t("config.afterKeep") }}</option>
                <option value="delete">{{ t("config.afterDelete") }}</option>
                <option value="move_to_archive">{{ t("config.afterMove") }}</option>
              </select>
            </div>
          </div>

          <div class="table-row-item">
            <div class="row-info-col">
              <div class="row-info-title">{{ t("config.archiveTitle") }}</div>
              <div class="row-info-sub">{{ t("config.archiveSub") }}</div>
            </div>
            <div class="row-ctrl-col w-48">
              <input v-model="form.archive_dir" type="text" class="app-input mono" />
            </div>
          </div>

          <div class="table-row-item">
            <div class="row-info-col">
              <div class="row-info-title">{{ t("config.autoSliceTitle") }}</div>
              <div class="row-info-sub">{{ t("config.autoSliceSub") }}</div>
            </div>
            <div class="row-ctrl-col">
              <el-switch v-model="form.auto_slice" />
            </div>
          </div>
        </div>
      </div>

      <!-- 并发与超时控制 -->
      <div class="inset-card">
        <div class="card-head">
          <h3 class="card-title">{{ t("config.limitsTitle") }}</h3>
          <p class="card-hint">{{ t("config.limitsHint") }}</p>
        </div>

        <div class="table-list-divide">
          <div class="table-row-item">
            <div class="row-info-col">
              <div class="row-info-title">{{ t("config.concurrencyTitle") }}</div>
              <div class="row-info-sub">{{ t("config.concurrencySub") }}</div>
            </div>
            <div class="row-ctrl-col w-28">
              <input
                v-model.number="form.concurrency"
                type="number"
                min="1"
                max="32"
                class="app-input mono"
              />
            </div>
          </div>

          <div class="table-row-item">
            <div class="row-info-col">
              <div class="row-info-title">{{ t("config.retriesTitle") }}</div>
              <div class="row-info-sub">{{ t("config.retriesSub") }}</div>
            </div>
            <div class="row-ctrl-col w-28">
              <input
                v-model.number="form.max_retries"
                type="number"
                min="1"
                max="20"
                class="app-input mono"
              />
            </div>
          </div>

          <div class="table-row-item">
            <div class="row-info-col">
              <div class="row-info-title">{{ t("config.scheduleTitle") }}</div>
              <div class="row-info-sub">{{ scheduleOrderHint }}</div>
            </div>
            <div class="row-ctrl-col w-36">
              <select v-model="form.schedule_order" class="app-select">
                <option value="oldest">{{ t("config.order.oldestOpt") }}</option>
                <option value="natural">{{ t("config.order.naturalOpt") }}</option>
                <option value="created">{{ t("config.order.createdOpt") }}</option>
                <option value="smallest">{{ t("config.order.smallestOpt") }}</option>
              </select>
            </div>
          </div>

          <div class="table-row-item">
            <div class="row-info-col">
              <div class="row-info-title">{{ t("config.timeoutTitle") }}</div>
              <div class="row-info-sub">{{ t("config.timeoutSub") }}</div>
            </div>
            <div class="row-ctrl-col w-36 input-with-unit-box">
              <input
                v-model.number="form.upload_timeout_seconds"
                type="number"
                min="30"
                class="app-input mono"
              />
              <span class="unit-label">{{ t("common.seconds") }}</span>
            </div>
          </div>

          <div class="table-row-item">
            <div class="row-info-col">
              <div class="row-info-title">{{ t("config.assignedTitle") }}</div>
              <div class="row-info-sub">{{ t("config.assignedSub") }}</div>
            </div>
            <div class="row-ctrl-col w-36 input-with-unit-box">
              <input
                v-model.number="form.assigned_timeout_seconds"
                type="number"
                min="30"
                class="app-input mono"
              />
              <span class="unit-label">{{ t("common.seconds") }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ── 4. Google Drive (可选) ── -->
    <div v-if="ENABLE_GOOGLE_DRIVE && panel === 'drive'" class="panel-section-group">
      <div class="inset-card">
        <div class="card-head-row">
          <div>
            <h3 class="card-title">{{ t("config.driveTitle") }}</h3>
            <p class="card-hint">{{ t("config.driveHint") }}</p>
          </div>
          <button type="button" class="btn-light-green" @click="showAddDrive = true; newDriveName = ''; newDriveId = ''">
            {{ t("config.driveAdd") }}
          </button>
        </div>

        <div class="chats-list-wrap">
          <div v-if="form.drive_folders.length === 0" class="empty-list-notice">
            {{ t("config.driveEmpty") }}
          </div>
          <div
            v-for="item in form.drive_folders"
            :key="item.folder_id"
            class="chat-row-item"
          >
            <div class="chat-row-main">
              <div class="chat-title-line">
                <span class="chat-main-name">{{ item.name.trim() || item.folder_id }}</span>
                <span class="chat-mono-id">({{ item.folder_id }})</span>
              </div>
            </div>
            <div class="chat-row-actions">
              <button
                type="button"
                class="btn-danger-text"
                @click="removeDrive(item.folder_id)"
              >
                {{ t("common.delete") }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>



    <el-dialog
      :model-value="topicChatId != null"
      :title="t('config.topicDialog')"
      width="480px"
      append-to-body
      @close="topicChatId = null"
    >
      <p v-if="topicLoading" class="route-hint">{{ t("config.topicLoading") }}</p>
      <p v-else-if="!topicForum" class="route-hint">{{ topicReason || t("config.topicNone") }}</p>
      <template v-else>
        <div class="topic-create">
          <el-input v-model="topicTitle" :placeholder="t('config.topicName')" @keyup.enter="onCreateTopic" />
          <el-button @click="onCreateTopic">{{ t("config.topicCreate") }}</el-button>
          <el-button @click="reloadTopics(true)">{{ t("common.refresh") }}</el-button>
        </div>
        <div v-if="topicItems.length === 0" class="route-hint">{{ t("config.topicEmpty") }}</div>
        <div v-for="topic in topicItems" :key="topic.topic_id" class="map-card fallback">
          <template v-if="topicRenameId === topic.topic_id">
            <el-input v-model="topicRenameTitle" />
            <el-button size="small" @click="onRenameTopic(topic.topic_id)">{{ t("common.save") }}</el-button>
          </template>
          <template v-else>
            <div class="map-name">{{ topic.title || t("config.topicUntitled") }}</div>
            <div class="map-dest">
              <span class="map-id">{{ topic.topic_id }}</span>
              <el-button size="small" text @click="topicRenameId = topic.topic_id; topicRenameTitle = topic.title">{{ t("config.topicRename") }}</el-button>
              <el-button size="small" text type="danger" @click="onDeleteTopic(topic.topic_id)">{{ t("common.delete") }}</el-button>
            </div>
          </template>
        </div>
      </template>
    </el-dialog>

    <DirectoryPolicyModal
      :model-value="stylePath !== ''"
      :path="stylePath"
      @update:model-value="stylePath = $event ? stylePath : ''"
      @saved="load"
    />

    <el-dialog v-model="showAddChat" :title="t('config.chatDialog')" width="440px" append-to-body>
      <p v-if="dialogReason" class="route-hint">{{ dialogReason }}</p>
      <template v-else>
        <div class="dialog-tools">
          <el-input v-model="chatQuery" :placeholder="t('config.chatSearch')" clearable />
          <el-button :loading="addingChat" @click="syncChats">{{ t("common.refresh") }}</el-button>
        </div>
        <div v-loading="addingChat" class="dialog-list">
          <button
            v-for="chat in filteredDialogs"
            :key="chat.id"
            type="button"
            class="dialog-row"
            :disabled="form.chats.some((item) => item.chat_id === chat.id)"
            @click="pickChat(chat)"
          >
            <span class="map-name">{{ chat.title || chat.id }}</span>
            <span class="map-chip">{{ chat.type === "channel" ? t("config.channel") : t("config.group") }}</span>
            <span v-if="form.chats.some((item) => item.chat_id === chat.id)" class="map-chip">{{ t("config.addedChip") }}</span>
          </button>
          <p v-if="!addingChat && filteredDialogs.length === 0" class="route-empty">{{ t("config.chatEmpty") }}</p>
        </div>
      </template>
      <template #footer>
        <el-button @click="showAddChat = false">{{ t("common.close") }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showAddDrive" :title="t('config.driveDialog')" width="420px" append-to-body>
      <el-form label-position="top">
        <el-form-item :label="t('config.driveName')">
          <el-input v-model="newDriveName" :placeholder="t('config.driveNamePlaceholder')" />
        </el-form-item>
        <el-form-item label="folder id">
          <el-input v-model="newDriveId" :placeholder="t('config.driveIdPlaceholder')" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAddDrive = false">{{ t("common.cancel") }}</el-button>
        <el-button type="primary" @click="confirmAddDrive">{{ t("common.add") }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.config-pane-wrap {
  width: 100%;
}

.panel-section-group {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.inset-card {
  background: #ffffff;
  border: 1px solid rgba(0, 0, 0, 0.06);
  border-radius: var(--radius-md, 10px);
  padding: 16px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.02);
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.card-head {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.card-head-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.card-title {
  margin: 0;
  font-size: 12px;
  font-weight: 700;
  color: #111827;
  text-transform: uppercase;
  letter-spacing: 0.02em;
}

.card-hint {
  margin: 0;
  font-size: 12.5px;
  color: #6b7280;
  line-height: 1.5;
}

.form-field-sm {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.form-label {
  font-size: 13px;
  font-weight: 600;
  color: #374151;
}

.app-input {
  width: 100%;
  box-sizing: border-box;
  padding: 6px 10px;
  background: #f9fafb;
  border: 1px solid rgba(0, 0, 0, 0.1);
  border-radius: var(--radius-sm, 6px);
  font-size: 13px;
  color: #111827;
  outline: none;
  transition: all 0.15s ease;
}

.app-input:focus {
  background: #ffffff;
  border-color: #28995a;
}

.app-input.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.max-w-sm {
  max-width: 320px;
}

.btn-light-green {
  padding: 5px 12px;
  font-size: 12.5px;
  font-weight: 600;
  background: rgba(40, 153, 90, 0.1);
  color: #28995a;
  border: 0;
  border-radius: var(--radius-sm, 6px);
  cursor: pointer;
  transition: all 0.15s ease;
  white-space: nowrap;
}

.btn-light-green:hover {
  background: rgba(40, 153, 90, 0.18);
}

.chats-list-wrap {
  display: flex;
  flex-direction: column;
}

.chat-row-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 0;
  border-top: 1px solid rgba(0, 0, 0, 0.05);
  font-size: 12px;
}

.chat-row-item:first-child {
  border-top: 0;
  padding-top: 2px;
}

.chat-row-main {
  min-width: 0;
}

.chat-title-line {
  font-weight: 700;
  color: #111827;
  display: flex;
  align-items: center;
  gap: 6px;
}

.chat-main-name {
  color: #111827;
}

.chat-mono-id {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-weight: 400;
  color: #9ca3af;
  font-size: 12px;
}

.chat-sub-line {
  font-size: 12.5px;
  color: #6b7280;
  margin-top: 2px;
}

.chat-row-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.btn-outline-sm {
  padding: 4px 10px;
  background: #f9fafb;
  border: 1px solid rgba(0, 0, 0, 0.1);
  border-radius: var(--radius-sm, 6px);
  font-size: 12px;
  color: #374151;
  cursor: pointer;
  transition: all 0.15s ease;
}

.btn-outline-sm:hover {
  background: #f3f4f6;
}

.btn-danger-text {
  border: 0;
  background: transparent;
  color: #ef4444;
  font-size: 12.5px;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: var(--radius-sm, 6px);
  transition: all 0.15s ease;
}

.btn-danger-text:hover {
  color: #b91c1c;
  background: #fee2e2;
}

.empty-list-notice {
  padding: 16px 0;
  text-align: center;
  font-size: 13px;
  color: #9ca3af;
}

.card-switch-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.paths-list-wrap {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.path-card-item {
  padding: 10px 12px;
  border-radius: var(--radius-sm, 6px);
  background: #f9fafb;
  border: 1px solid rgba(0, 0, 0, 0.05);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.path-card-left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.path-status-dot {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full, 9999px);
  flex-shrink: 0;
}

.path-status-dot.ok {
  background: #28995a;
}

.path-status-dot.err {
  background: #f59e0b;
}

.path-card-text {
  min-width: 0;
}

.path-card-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-weight: 700;
  color: #1f2937;
  font-size: 13px;
}

.path-card-sub {
  font-size: 12px;
  color: #9ca3af;
  margin-top: 1px;
}

.ext-chips-box {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 8px;
  background: #f9fafb;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: var(--radius-sm, 6px);
  align-items: center;
}

.ext-chip {
  padding: 3px 8px;
  border-radius: var(--radius-sm, 6px);
  background: #ffffff;
  border: 1px solid rgba(0, 0, 0, 0.08);
  color: #374151;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 12px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.chip-remove {
  border: 0;
  background: transparent;
  color: #9ca3af;
  cursor: pointer;
  font-size: 11.5px;
  padding: 0;
}

.chip-remove:hover {
  color: #ef4444;
}

.ext-add-row {
  display: flex;
  align-items: center;
  gap: 4px;
}

.ext-inline-input {
  width: 76px;
  padding: 3px 7px;
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, monospace;
  background: #ffffff;
  border: 1px solid rgba(0, 0, 0, 0.1);
  border-radius: var(--radius-sm, 6px);
  outline: none;
}

.ext-inline-input:focus {
  border-color: #28995a;
}

.ext-inline-btn {
  border: 0;
  background: transparent;
  color: #28995a;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  padding: 2px 4px;
}

.ext-inline-btn:hover {
  text-decoration: underline;
}

.card-split-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.input-with-unit-box {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.unit-label {
  font-size: 12.5px;
  color: #9ca3af;
}

.w-28 {
  width: 80px;
}

.w-32 {
  width: 100px;
}

.w-36 {
  width: 120px;
}

.w-48 {
  width: 180px;
}

.w-56 {
  width: 220px;
}

.table-list-divide {
  display: flex;
  flex-direction: column;
}

.table-row-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 0;
  border-top: 1px solid rgba(0, 0, 0, 0.05);
  font-size: 13px;
}

.table-row-item:first-child {
  border-top: 0;
  padding-top: 2px;
}

.table-row-item.vertical {
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
}

.row-info-title {
  font-weight: 600;
  color: #1f2937;
}

.row-info-sub {
  font-size: 12px;
  color: #9ca3af;
  margin-top: 1px;
}

.row-top-flex {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.grid-status-tip-inline {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #059669;
  font-weight: 500;
  background: rgba(16, 185, 129, 0.08);
  padding: 3px 8px;
  border-radius: var(--radius-sm, 6px);
}

.grid-status-tip-inline.is-err {
  color: #dc2626;
  background: rgba(239, 68, 68, 0.08);
}

.grid-layout-input-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 4px;
}

.grid-spec-input {
  width: 160px;
  flex-shrink: 0;
}

.grid-presets-inline-title {
  font-size: 12px;
  color: #6b7280;
  margin-right: 2px;
  white-space: nowrap;
}

.token-chips-group {
  display: flex;
  align-items: center;
  gap: 4px;
}

.token-btn {
  padding: 3px 8px;
  border-radius: var(--radius-sm, 6px);
  background: #f3f4f6;
  border: 0;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 12px;
  color: #374151;
  cursor: pointer;
  transition: all 0.15s ease;
}

.token-btn:hover {
  background: #e5e7eb;
}

.token-btn.active {
  background: rgba(40, 153, 90, 0.15);
  color: #28995a;
  font-weight: 700;
}

.app-select {
  width: 100%;
  box-sizing: border-box;
  padding: 7px 10px;
  background: #f9fafb;
  border: 1px solid rgba(0, 0, 0, 0.1);
  border-radius: var(--radius-sm, 6px);
  font-size: 13px;
  color: #111827;
  outline: none;
  transition: all 0.15s ease;
}

.app-select:focus {
  background: #ffffff;
  border-color: #28995a;
}

.config-block {
  min-width: 0;
}

.hint {
  margin-bottom: 18px;
}

.form :deep(.el-input-number) {
  width: 100%;
}

.section + .section {
  margin-top: 8px;
  padding-top: 16px;
  border-top: 1px solid var(--border-light);
}

.section-title {
  margin: 0 0 12px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}

.section-title.later {
  margin-top: 16px;
}

.route-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 14px 0 6px;
}

.route-head .section-title {
  margin: 0;
}

.route-hint,
.route-empty {
  margin: 0 0 10px;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.5;
}

.route-row {
  display: grid;
  grid-template-columns: 0.9fr 1.5fr 0.9fr 0.8fr auto;
  gap: 0 12px;
  align-items: end;
}

.route-del {
  margin-bottom: 18px;
}

.map-card {
  display: block;
  width: 100%;
  margin-bottom: 8px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md, 10px);
  background: var(--surface);
  text-align: left;
  cursor: pointer;
  transition: border-color 0.15s cubic-bezier(0.32, 0.72, 0, 1);
}

.map-card:hover,
.map-card.active {
  border-color: rgba(40, 153, 90, 0.35);
}

.map-card.fallback {
  cursor: default;
  background: var(--col-pending, #f3f5f3);
}

.map-card.muted {
  opacity: 0.55;
}

.alias-input {
  margin-top: 8px;
}

.path-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 8px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md, 10px);
  background: var(--surface);
}

.path-left {
  min-width: 0;
  flex: 1;
  font-size: 12px;
  font-family: ui-monospace, "SF Mono", "JetBrains Mono", monospace;
  color: var(--text);
  word-break: break-all;
}

.path-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.path-select {
  width: 180px;
}

.dialog-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 360px;
  margin-top: 12px;
  overflow-y: auto;
}

.dialog-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md, 10px);
  background: var(--surface);
  text-align: left;
  cursor: pointer;
}

.dialog-row:disabled {
  cursor: default;
  opacity: 0.55;
}

.dialog-row:hover:not(:disabled) {
  border-color: rgba(40, 153, 90, 0.35);
}

.map-name {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text);
}

.map-path {
  margin-top: 4px;
  font-size: 12px;
  font-family: ui-monospace, "SF Mono", "JetBrains Mono", monospace;
  color: var(--text-secondary);
  word-break: break-all;
}

.map-dest {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.map-id {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  font-family: ui-monospace, "SF Mono", "JetBrains Mono", monospace;
  color: var(--text);
}

.map-chip {
  font-size: 11px;
  color: var(--text-secondary);
}

.cols {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 16px;
}

.wide {
  grid-column: 1 / -1;
}

.grow {
  width: 100%;
}

.path-hints {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.path-hints li {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 0;
  word-break: break-all;
}

.path-hints .ok {
  color: var(--ok);
  flex-shrink: 0;
  font-weight: 500;
}

.path-hints .bad {
  color: var(--bad);
  flex-shrink: 0;
  font-weight: 500;
}

.fs-tree-wrapper {
  margin-top: 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg, 14px);
  background: var(--surface);
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}

.fs-tree {
  padding: 8px 10px;
  background: transparent;
  font-size: 13px;
}

:deep(.el-tree-node__content) {
  height: auto !important;
  min-height: 40px;
  padding-top: 3px;
  padding-bottom: 3px;
  border-radius: var(--radius-sm, 6px);
  transition: background-color 0.15s ease;
}

:deep(.el-tree-node__content:hover) {
  background-color: var(--hover);
}

.tree-node-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  padding-right: 4px;
}

.node-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}

.node-left.is-dir {
  align-items: center;
}

.node-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.node-dest-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 10px;
  border-radius: var(--radius-full, 9999px);
  font-size: 12px;
  line-height: 1.4;
  background: rgba(40, 153, 90, 0.08);
  color: #28995a;
  border: 1px solid rgba(40, 153, 90, 0.22);
  white-space: nowrap;
  font-weight: 500;
  user-select: none;
}

.node-dest-badge.is-miss {
  background: rgba(220, 38, 38, 0.08);
  color: #dc2626;
  border-color: rgba(220, 38, 38, 0.2);
}

.caption-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.caption-chip {
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  border-radius: var(--radius-full, 9999px);
  padding: 2px 9px;
  font-size: 12px;
  cursor: pointer;
}

.caption-chip.on {
  background: var(--accent-soft);
  border-color: rgba(40, 153, 90, 0.35);
  color: var(--accent);
}

.topic-pick {
  margin-top: 8px;
}

.topic-create {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
}

.node-icon {
  font-size: 14px;
  flex-shrink: 0;
}

.node-name {
  font-size: 13.5px;
  font-weight: 500;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.node-name.is-root {
  font-weight: 600;
  font-family: ui-monospace, "SF Mono", "JetBrains Mono", monospace;
}

.node-badge {
  font-size: 11.5px;
  padding: 1.5px 7px;
  border-radius: var(--radius-sm, 6px);
  flex-shrink: 0;
}

.root-badge {
  background: var(--accent-soft);
  color: var(--accent);
  border: 1px solid rgba(40, 153, 90, 0.25);
  font-weight: 500;
}

.file-size {
  font-size: 12px;
  color: var(--text-secondary);
  font-family: ui-monospace, "SF Mono", monospace;
  flex-shrink: 0;
}

.file-ext-unsupported {
  font-size: 11.5px;
  padding: 1.5px 6px;
  border-radius: var(--radius-sm, 6px);
  background: #f3f4f6;
  color: #6b7280;
  border: 1px solid #e5e7eb;
  flex-shrink: 0;
}

.file-status {
  font-size: 11.5px;
  padding: 1.5px 7px;
  border-radius: var(--radius-sm, 6px);
  font-weight: 500;
  flex-shrink: 0;
}

.status-uploading {
  background: #eff6ff;
  color: #2563eb;
  border: 1px solid #bfdbfe;
}

.status-success {
  background: #ecfdf5;
  color: #059669;
  border: 1px solid #a7f3d0;
}

.status-pending {
  background: #fffbeb;
  color: #d97706;
  border: 1px solid #fde68a;
}

.status-failed {
  background: #fef2f2;
  color: #dc2626;
  border: 1px solid #fecaca;
}

.status-unmatched {
  background: #f3f4f6;
  color: #4b5563;
  border: 1px solid #e5e7eb;
}

.node-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.route-pill {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: var(--radius-sm, 6px);
  font-weight: 500;
  white-space: nowrap;
}

.route-pill.explicit {
  background: var(--accent-soft);
  color: var(--accent);
  border: 1px solid rgba(40, 153, 90, 0.3);
}

.route-pill.inherited {
  background: rgba(0, 0, 0, 0.04);
  color: var(--text-secondary);
  border: 1px solid var(--border);
}

.route-pill.unmatched {
  background: #fef2f2;
  color: #dc2626;
  border: 1px solid #fecaca;
}

.btn-refresh {
  font-size: 12px;
  padding: 0 4px !important;
  opacity: 0.65;
  transition: opacity 0.15s ease;
}

.btn-refresh:hover {
  opacity: 1;
}

@media (max-width: 900px) {
  .cols {
    grid-template-columns: 1fr 1fr;
  }
}

@media (max-width: 768px) {
  .head {
    flex-wrap: wrap;
    gap: 8px;
  }

  .cols {
    grid-template-columns: 1fr;
  }

  .tree-node-row {
    flex-wrap: wrap;
    gap: 6px;
    padding: 4px 0;
    height: auto !important;
  }

  .node-left {
    width: 100%;
    flex: 1 1 100%;
  }

  .node-right {
    width: 100%;
    padding-left: 22px;
    justify-content: flex-start;
    flex-wrap: wrap;
    gap: 6px;
  }

  .path-select {
    width: 100% !important;
    max-width: 220px;
  }

  :deep(.el-tree-node__content) {
    height: auto !important;
    min-height: 36px;
    padding-top: 4px;
    padding-bottom: 4px;
  }

  :deep(.el-dialog) {
    max-width: calc(100vw - 24px) !important;
    width: auto !important;
    margin: 16px auto !important;
    border-radius: var(--radius-xl, 16px);
  }
}
</style>

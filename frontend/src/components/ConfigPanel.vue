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

const preferSmallFiles = computed({
  get: () => form.schedule_order === "smallest",
  set: (enabled: boolean) => {
    form.schedule_order = enabled ? "smallest" : "oldest";
  },
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
    topicReason.value = error instanceof Error ? error.message : "读取话题失败";
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
    ElMessage.error(error instanceof Error ? error.message : "无法新建话题");
  }
}

async function onRenameTopic(topicId: number): Promise<void> {
  if (topicChatId.value == null || !topicRenameTitle.value.trim()) return;
  try {
    await renameChatTopic(topicChatId.value, topicId, topicRenameTitle.value.trim());
    topicRenameId.value = null;
    await reloadTopics(false);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "无法改名");
  }
}

async function onDeleteTopic(topicId: number): Promise<void> {
  if (topicChatId.value == null) return;
  try {
    await deleteChatTopic(topicChatId.value, topicId);
    await reloadTopics(false);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "无法删除话题");
  }
}



const showAddDrive = ref(false);
const newDriveName = ref("");
const newDriveId = ref("");

function confirmAddDrive(): void {
  // 校验并追加 Drive 文件夹，供目录路由选择器使用。
  const folderId = newDriveId.value.trim();
  if (!folderId) {
    ElMessage.error("请填写 folder id");
    return;
  }
  if (form.drive_folders.some((item) => item.folder_id === folderId)) {
    ElMessage.error("该文件夹已在列表中");
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
    dialogReason.value = data.online ? "" : data.reason || "请先在监控页用个人账号登录";
  } catch (error) {
    dialogChats.value = [];
    dialogReason.value = error instanceof Error ? error.message : "无法读取群列表";
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
    dialogReason.value = data.online ? "" : data.reason || "请先在监控页用个人账号登录";
  } catch (error) {
    dialogReason.value = error instanceof Error ? error.message : "同步群列表失败";
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
    ElMessage.success("已保存群组配置");
  } catch (error) {
    form.chats = previousChats;
    form.routes = previousRoutes;
    ElMessage.error(error instanceof Error ? error.message : "保存群组配置失败");
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
    ElMessage.error(error instanceof Error ? error.message : "读取配置失败");
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
    ElMessage.success("已写入 upload.toml，配置立即生效");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "保存失败");
  } finally {
    saving.value = false;
  }
}

async function promptAddObserverPath(): Promise<void> {
  try {
    const { value } = await ElMessageBox.prompt(
      "请输入本地监听目录的绝对或相对路径。新路径默认暂停，打开开关并保存后才会扫描。",
      "添加监听路径",
      {
        confirmButtonText: "添加",
        cancelButtonText: "取消",
        inputPlaceholder: "例如 D:\\Downloads\\Telegram 或 download",
        inputValidator: (val) => {
          if (!val || !val.trim()) return "路径不能为空";
          if (form.observer_roots.some((p) => samePath(p.path, val.trim()))) return "该路径已在监听列表中";
          return true;
        },
      },
    );
    if (value && value.trim()) {
      const trimmed = value.trim();
      form.observer_roots.push(pausedObserverRoot(trimmed));
      form.observer_paths = form.observer_roots.map((r) => r.path);
      ElMessage.success("已加入列表且默认暂停。打开这一行的开关并保存后才会扫描。");
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
      return { ok: false, text: "状态: 监控已暂停（可在此处重新开启）" };
    }
    if (root.ok) {
      return { ok: true, text: "状态: 正常监听中 · 读写权限正常" };
    }
    return { ok: false, text: `状态: 异常 · ${root.error || "目录不可读或不存在"}` };
  }
  return { ok: true, text: "状态: 监听配置已就绪" };
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
          <h3 class="card-title">默认投递目标 (chat_id)</h3>
          <p class="card-hint">全局默认上传的 Telegram 群组或频道 ID (纯数字，不可为 0)。目录规则未命中时使用此默认目标。</p>
        </div>

        <div class="form-field-sm">
          <label class="form-label">默认 chat_id</label>
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
            <h3 class="card-title">已配置的目标列表 (chats)</h3>
            <p class="card-hint">供各监控目录分配路由的目标列表，支持自定义别名与话题。</p>
          </div>
          <button type="button" class="btn-light-green" @click="openAddChat">
            + 添加群组 / 频道
          </button>
        </div>

        <div class="chats-list-wrap">
          <div v-if="form.chats.length === 0" class="empty-list-notice">
            还没有配置群组或频道。点击右上角添加。
          </div>
          <div
            v-for="item in form.chats"
            :key="item.chat_id"
            class="chat-row-item"
          >
            <div class="chat-row-main">
              <div class="chat-title-line">
                <span class="chat-main-name">{{ item.alias.trim() || item.title.trim() || "未命名目标" }}</span>
                <span class="chat-mono-id">({{ item.chat_id }})</span>
              </div>
              <div v-if="item.alias.trim() && item.title.trim()" class="chat-sub-line">
                别名: {{ item.alias }} · 官方名: {{ item.title }}
              </div>
            </div>
            <div class="chat-row-actions">
              <button
                type="button"
                class="btn-outline-sm"
                @click="openTopicManager(item.chat_id)"
              >
                管理话题
              </button>
              <button
                type="button"
                class="btn-danger-text"
                @click="removeChat(item.chat_id)"
              >
                删除
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- 全局话题新建开关 -->
      <div class="inset-card">
        <div class="card-switch-row">
          <div>
            <h3 class="card-title">全局话题新建开关 (topic_creation_enabled)</h3>
            <p class="card-hint">当目标群组开启论坛话题模式且目录策略未指定固定话题时，是否允许自动创建新话题（如按文件夹名）。</p>
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
            <h3 class="card-title">已挂载的监听根路径 (observer_roots)</h3>
            <p class="card-hint">系统 watchdog 实时监听的物理根目录，支持独立启停。保存后热重载生效。</p>
          </div>
          <button type="button" class="btn-light-green" @click="promptAddObserverPath">
            + 添加监听路径
          </button>
        </div>

        <div class="paths-list-wrap">
          <div v-if="form.observer_roots.length === 0" class="empty-list-notice">
            暂无监听路径。点击右上角添加。
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
                active-text="开启"
                inactive-text="暂停"
                inline-prompt
              />
              <button
                type="button"
                class="btn-danger-text"
                @click="removeObserverPath(root.path)"
              >
                删除
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- 文件格式过滤白名单 -->
      <div class="inset-card">
        <div class="card-head">
          <h3 class="card-title">文件格式白名单 (watch_extensions)</h3>
          <p class="card-hint">仅监听匹配此后缀的文件。若列表为空则处理所有文件。</p>
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
              placeholder="如 mkv"
              class="ext-inline-input"
              @keyup.enter="addExtension"
            />
            <button type="button" class="ext-inline-btn" @click="addExtension">+ 添加格式</button>
          </div>
        </div>
      </div>

      <!-- 文件稳定静默超时 -->
      <div class="inset-card">
        <div class="card-split-row">
          <div>
            <h3 class="card-title">文件稳定超时 (stable_timeout_seconds)</h3>
            <p class="card-hint">文件最后修改后需静默等待的秒数，确保大文件下载或复制完全写盘后再触发上传。</p>
          </div>
          <div class="input-with-unit-box">
            <input
              v-model.number="form.stable_timeout_seconds"
              type="number"
              min="1"
              class="app-input mono w-32"
            />
            <span class="unit-label">秒</span>
          </div>
        </div>
      </div>
    </div>

    <!-- ── 3. 处理与收尾 (process) ── -->
    <div v-if="panel === 'process'" class="panel-section-group">
      <!-- 视频封面与说明模板 -->
      <div class="inset-card">
        <div class="card-head">
          <h3 class="card-title">视频封面与说明模板</h3>
          <p class="card-hint">未配置单独目录策略时继承的默认全局设定。</p>
        </div>

        <div class="table-list-divide">
          <div class="table-row-item">
            <div class="row-info-col">
              <div class="row-info-title">视频缩略图策略 (preview)</div>
              <div class="row-info-sub">自动为 MKV/MP4 生成预览封面</div>
            </div>
            <div class="row-ctrl-col w-56">
              <select v-model="form.preview" class="app-select">
                <option value="first_frame">首帧截图 (first_frame)</option>
                <option value="grid">九宫格剧情拼图 (grid)</option>
                <option value="off">关闭缩略图 (off)</option>
              </select>
            </div>
          </div>

          <div class="table-row-item">
            <div class="row-info-col">
              <div class="row-info-title">缩略图目录 (page_dir)</div>
              <div class="row-info-sub">临时生成缩略图的本地存放路径</div>
            </div>
            <div class="row-ctrl-col w-48">
              <input v-model="form.page_dir" type="text" class="app-input mono" />
            </div>
          </div>

          <div class="table-row-item vertical">
            <div class="row-top-flex">
              <div>
                <div class="row-info-title">全局说明模板 (caption_template)</div>
                <div class="row-info-sub">未设模板时默认采用此模板，留空则不写说明</div>
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
              placeholder="<b>{file_name}</b> | 规格: {size}"
              class="app-input mono"
            />
          </div>
        </div>
      </div>

      <!-- 上传完成与超限处理 -->
      <div class="inset-card">
        <div class="card-head">
          <h3 class="card-title">上传完成与超限处理</h3>
          <p class="card-hint">本地源文件处置与超过 2GB 大文件的自动化处理动作。</p>
        </div>

        <div class="table-list-divide">
          <div class="table-row-item">
            <div class="row-info-col">
              <div class="row-info-title">上传成功后处理 (after_success)</div>
              <div class="row-info-sub">文件完整送达 Telegram 后的动作</div>
            </div>
            <div class="row-ctrl-col w-56">
              <select v-model="form.after_success" class="app-select">
                <option value="keep">保留本地源文件 (keep)</option>
                <option value="delete">直接删除本地源文件 (delete)</option>
                <option value="move_to_archive">移入归档目录 (move_to_archive)</option>
              </select>
            </div>
          </div>

          <div class="table-row-item">
            <div class="row-info-col">
              <div class="row-info-title">归档目录 (archive_dir)</div>
              <div class="row-info-sub">选择移入归档时移动的目标子目录</div>
            </div>
            <div class="row-ctrl-col w-48">
              <input v-model="form.archive_dir" type="text" class="app-input mono" />
            </div>
          </div>

          <div class="table-row-item">
            <div class="row-info-col">
              <div class="row-info-title">超限自动切片 (auto_slice)</div>
              <div class="row-info-sub">新发现超过 2GB 的视频按最少段数自动切片</div>
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
          <h3 class="card-title">并发与超时控制</h3>
          <p class="card-hint">账号级任务并发与底层网络超时容错参数。</p>
        </div>

        <div class="table-list-divide">
          <div class="table-row-item">
            <div class="row-info-col">
              <div class="row-info-title">账号级上传并发 (concurrency)</div>
              <div class="row-info-sub">默认 1，文件内部由 FastTelethon 负责分块并发</div>
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
              <div class="row-info-title">最大重试次数 (max_retries)</div>
              <div class="row-info-sub">失败后自动重新排队的最大尝试次数 (默认 3)</div>
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
              <div class="row-info-title">小文件优先</div>
              <div class="row-info-sub">默认按发现顺序上传。打开后，待传队列里体积更小的文件先传。</div>
            </div>
            <div class="row-ctrl-col">
              <el-switch v-model="preferSmallFiles" />
            </div>
          </div>

          <div class="table-row-item">
            <div class="row-info-col">
              <div class="row-info-title">上传超时时间 (upload_timeout_seconds)</div>
              <div class="row-info-sub">单个文件传输的最长超时限制 (默认 1200 秒)</div>
            </div>
            <div class="row-ctrl-col w-36 input-with-unit-box">
              <input
                v-model.number="form.upload_timeout_seconds"
                type="number"
                min="30"
                class="app-input mono"
              />
              <span class="unit-label">秒</span>
            </div>
          </div>

          <div class="table-row-item">
            <div class="row-info-col">
              <div class="row-info-title">Worker 分配超时 (assigned_timeout_seconds)</div>
              <div class="row-info-sub">已指派 Worker 但未进入传输的最大等待时间 (默认 600 秒)</div>
            </div>
            <div class="row-ctrl-col w-36 input-with-unit-box">
              <input
                v-model.number="form.assigned_timeout_seconds"
                type="number"
                min="30"
                class="app-input mono"
              />
              <span class="unit-label">秒</span>
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
            <h3 class="card-title">Google Drive 文件夹</h3>
            <p class="card-hint">手动添加要投递的 Drive 文件夹。上传客户端接入前，这里的目标可以先选，文件会停在等待。</p>
          </div>
          <button type="button" class="btn-light-green" @click="showAddDrive = true; newDriveName = ''; newDriveId = ''">
            + 添加文件夹
          </button>
        </div>

        <div class="chats-list-wrap">
          <div v-if="form.drive_folders.length === 0" class="empty-list-notice">
            还没有文件夹。点击右上角添加。
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
                删除
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>



    <el-dialog
      :model-value="topicChatId != null"
      title="话题"
      width="480px"
      append-to-body
      @close="topicChatId = null"
    >
      <p v-if="topicLoading" class="route-hint">正在读取</p>
      <p v-else-if="!topicForum" class="route-hint">{{ topicReason || "这个群或频道没有话题。" }}</p>
      <template v-else>
        <div class="topic-create">
          <el-input v-model="topicTitle" placeholder="新话题名称" @keyup.enter="onCreateTopic" />
          <el-button @click="onCreateTopic">新建</el-button>
          <el-button @click="reloadTopics(true)">刷新</el-button>
        </div>
        <div v-if="topicItems.length === 0" class="route-hint">还没有话题。可以新建，或点刷新从 Telegram 读取。</div>
        <div v-for="topic in topicItems" :key="topic.topic_id" class="map-card fallback">
          <template v-if="topicRenameId === topic.topic_id">
            <el-input v-model="topicRenameTitle" />
            <el-button size="small" @click="onRenameTopic(topic.topic_id)">保存</el-button>
          </template>
          <template v-else>
            <div class="map-name">{{ topic.title || "未命名" }}</div>
            <div class="map-dest">
              <span class="map-id">{{ topic.topic_id }}</span>
              <el-button size="small" text @click="topicRenameId = topic.topic_id; topicRenameTitle = topic.title">改名</el-button>
              <el-button size="small" text type="danger" @click="onDeleteTopic(topic.topic_id)">删除</el-button>
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

    <el-dialog v-model="showAddChat" title="选择群或频道" width="440px" append-to-body>
      <p v-if="dialogReason" class="route-hint">{{ dialogReason }}</p>
      <template v-else>
        <div class="dialog-tools">
          <el-input v-model="chatQuery" placeholder="搜索名称" clearable />
          <el-button :loading="addingChat" @click="syncChats">刷新</el-button>
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
            <span class="map-chip">{{ chat.type === "channel" ? "频道" : "群" }}</span>
            <span v-if="form.chats.some((item) => item.chat_id === chat.id)" class="map-chip">已添加</span>
          </button>
          <p v-if="!addingChat && filteredDialogs.length === 0" class="route-empty">没有匹配的群或频道。</p>
        </div>
      </template>
      <template #footer>
        <el-button @click="showAddChat = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showAddDrive" title="添加 Drive 文件夹" width="420px" append-to-body>
      <el-form label-position="top">
        <el-form-item label="名称">
          <el-input v-model="newDriveName" placeholder="例如 备份" />
        </el-form-item>
        <el-form-item label="folder id">
          <el-input v-model="newDriveId" placeholder="Drive 文件夹 id" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAddDrive = false">取消</el-button>
        <el-button type="primary" @click="confirmAddDrive">添加</el-button>
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

.token-chips-group {
  display: flex;
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

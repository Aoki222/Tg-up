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

import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { useQueryClient } from "@tanstack/vue-query";
import { fetchDialogChats, fetchFsNodes, fetchSettings, saveSettings, syncDialogChats } from "../api";
import type { DialogChat } from "../api";
import type { FsNode, UploadConfig } from "../types";
import { formatBytes } from "../format";
import { ENABLE_GOOGLE_DRIVE } from "../features";
import { useUnmatchedFiles } from "../composables/useUnmatched";

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
  observer_paths: ["download"],
  observer_path_infos: [],
  page_dir: "page",
  archive_dir: "uploaded",
  preview: "first_frame",
  caption_template: "",
  topic_creation_enabled: true,
  after_success: "keep",
  auto_slice: false,
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

// ── 脏检查机制 ─────────────────────────────────────────────────

/**
 * 将配置对象序列化为规格化的 JSON 字符串（数组预先 trim 并排序，消除乱序干扰）
 */
function snapshotOf(config: UploadConfig): string {
  return JSON.stringify({
    ...config,
    observer_paths: [...config.observer_paths].map((item) => item.trim()).sort(),
    watch_extensions: [...config.watch_extensions].map((item) => item.trim()).sort(),
    routes: [...(config.routes ?? [])]
      .map((item) => ({
        name: (item.name || "").trim(),
        path: (item.path || "").trim(),
        chat_id: item.chat_id,
        topic_enabled: item.topic_enabled,
        caption_template: item.caption_template ?? null,
        preview: item.preview ?? null,
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
const styleDest = ref("");
const styleCaptionMode = ref<"inherit" | "none" | "custom">("inherit");
const styleCaption = ref("");
const stylePreview = ref<"inherit" | "off" | "first_frame" | "grid">("inherit");
const captionBox = ref<{ textarea?: HTMLTextAreaElement } | null>(null);

const CAPTION_CHIPS = [
  { label: "文件名", token: "{file_name}" },
  { label: "主文件名", token: "{stem}" },
  { label: "后缀", token: "{ext}" },
  { label: "所在目录", token: "{folder}" },
  { label: "相对路径", token: "{rel_path}" },
  { label: "路由名", token: "{route}" },
  { label: "大小", token: "{size}" },
  { label: "日期", token: "{date}" },
] as const;

function samePath(left: string, right: string): boolean {
  // 统一斜杠、去掉末尾分隔符并忽略大小写，用于匹配目录路由。
  return left.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase() === right.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

const { files: unmatchedFiles } = useUnmatchedFiles();

function chatLabel(chatId: number): string {
  // 优先显示用户配置的别名，其次显示 Telegram 标题，最后回退到 chat_id。
  if (!chatId) return "未命中";
  const item = form.chats.find((entry) => entry.chat_id === chatId);
  if (!item) return String(chatId);
  return item.alias.trim() || item.title.trim() || String(chatId);
}

function pathDestKey(path: string): string {
  // 将监听目录映射为前端选择器使用的 tg:<id> 或 gd:<folder_id> 标识。
  const route = form.routes.find((item) => item.enabled && samePath(item.path, path));
  if (!route) return "";
  if ((route.platform || "telegram") === "gdrive") return `gd:${route.dest_id}`;
  if (!route.chat_id) return "";
  return `tg:${route.chat_id}`;
}

function pathDestLabel(path: string): string {
  // 把路由标识转换为界面上的群组别名或 Drive 文件夹名称。
  const key = pathDestKey(path);
  if (!key) return "未命中";
  if (key.startsWith("gd:")) {
    const folderId = key.slice(3);
    const folder = form.drive_folders.find((item) => item.folder_id === folderId);
    return folder?.name.trim() || folderId;
  }
  return chatLabel(Number(key.slice(3)));
}

function setPathDest(path: string, key: string): void {
  // 修改单个目录的目标路由；空目标表示移除该目录路由。
  const index = form.routes.findIndex((item) => samePath(item.path, path));
  if (!key) {
    if (index >= 0) form.routes.splice(index, 1);
    return;
  }
  const next = key.startsWith("gd:")
    ? { platform: "gdrive", dest_id: key.slice(3), chat_id: 0 }
    : { platform: "telegram", dest_id: key.slice(3), chat_id: Number(key.slice(3)) };
  if (index >= 0) {
    Object.assign(form.routes[index], next, { enabled: true });
  } else {
    form.routes.push({
      name: "",
      path,
      topic_enabled: null,
      caption_template: null,
      preview: null,
      enabled: true,
      ...next,
    });
  }
}

function folderTail(path: string): string {
  const parts = path.replace(/\\/g, "/").split("/").filter(Boolean);
  return parts[parts.length - 1] || path;
}

function captionWord(path: string): string {
  const route = form.routes.find((item) => item.enabled && samePath(item.path, path));
  if (!route || route.caption_template == null) return "跟随";
  if (route.caption_template === "") return "不要说明";
  return "自己写";
}

function previewWord(path: string): string {
  const route = form.routes.find((item) => item.enabled && samePath(item.path, path));
  const mode = route?.preview;
  if (mode === "off") return "关闭";
  if (mode === "first_frame") return "首帧";
  if (mode === "grid") return "网格";
  return "跟随";
}

function routeSummary(data: FsNode): string {
  if (hasExplicitRoute(data.path)) {
    return `专属 · ${pathDestLabel(data.path)} · 说明：${captionWord(data.path)} · 封面：${previewWord(data.path)}`;
  }
  if (data.current_route?.matched) {
    const from = data.current_route.inherited_from
      ? folderTail(data.current_route.inherited_from)
      : data.current_route.dest_name || "上级";
    return `继承自 ${from} · 说明：跟随 · 封面：跟随`;
  }
  return "未命中";
}

function readRouteStyle(path: string): void {
  const route = form.routes.find((item) => item.enabled && samePath(item.path, path));
  styleDest.value = pathDestKey(path);
  if (!route || route.caption_template == null) {
    styleCaptionMode.value = "inherit";
    styleCaption.value = "";
  } else if (route.caption_template === "") {
    styleCaptionMode.value = "none";
    styleCaption.value = "";
  } else {
    styleCaptionMode.value = "custom";
    styleCaption.value = route.caption_template;
  }
  stylePreview.value = route?.preview ?? "inherit";
}

function openRouteSettings(path: string): void {
  readRouteStyle(path);
  stylePath.value = path;
}

function chipOn(token: string): boolean {
  return styleCaption.value.includes(token);
}

async function toggleChip(token: string): Promise<void> {
  const current = styleCaption.value;
  if (current.includes(token)) {
    styleCaption.value = current
      .split(token)
      .join("")
      .replace(/[ \t]{2,}/g, " ")
      .replace(/\s+\/\s+/g, " / ")
      .trim();
    return;
  }
  const box = captionBox.value?.textarea;
  if (box && document.activeElement === box) {
    const start = box.selectionStart ?? current.length;
    const end = box.selectionEnd ?? start;
    const before = current.slice(0, start);
    const after = current.slice(end);
    const pad = before && !/\s$/.test(before) ? " " : "";
    styleCaption.value = `${before}${pad}${token}${after}`;
    await nextTick();
    const pos = (before + pad + token).length;
    box.focus();
    box.setSelectionRange(pos, pos);
    return;
  }
  styleCaption.value = current ? `${current} ${token}` : token;
}

const stylePreviewText = computed(() => {
  const folder = folderTail(stylePath.value) || "目录";
  const sample: Record<string, string> = {
    file_name: "电影.mp4",
    stem: "电影",
    ext: "mp4",
    folder,
    rel_path: "电影.mp4",
    route: folder,
    size: "1.4 GB",
    date: new Date().toISOString().slice(0, 10),
  };
  const template = styleCaption.value;
  if (!template.trim()) return "不写说明";
  let position = 0;
  let text = "";
  const pattern = /\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)\}/g;
  for (const match of template.matchAll(pattern)) {
    const index = match.index ?? 0;
    text += template.slice(position, index);
    const token = match[0];
    const name = match[1];
    if (token === "{{") text += "{";
    else if (token === "}}") text += "}";
    else if (name && name in sample) text += sample[name];
    else text += token;
    position = index + token.length;
  }
  text += template.slice(position);
  return text.trim() || "不写说明";
});

function applyRouteSettings(): void {
  const path = stylePath.value;
  if (!path) return;
  if (!styleDest.value) {
    setPathDest(path, "");
    stylePath.value = "";
    return;
  }
  setPathDest(path, styleDest.value);
  const route = form.routes.find((item) => item.enabled && samePath(item.path, path));
  if (!route) {
    stylePath.value = "";
    return;
  }
  if (styleCaptionMode.value === "inherit") route.caption_template = null;
  else if (styleCaptionMode.value === "none") route.caption_template = "";
  else route.caption_template = styleCaption.value.slice(0, 2000);
  route.preview = stylePreview.value === "inherit" ? null : stylePreview.value;
  stylePath.value = "";
}

function hasExplicitRoute(path: string): boolean {
  return form.routes.some((item) => item.enabled && samePath(item.path, path));
}

const queryClient = useQueryClient();
const treeRef = ref();

async function loadFsNodes(node: any, resolve: (data: FsNode[]) => void): Promise<void> {
  const targetPath = node.level === 0 ? "" : (node.data as FsNode).path;
  try {
    const items = await queryClient.fetchQuery({
      queryKey: ["fs-nodes", targetPath],
      queryFn: () => fetchFsNodes(targetPath),
    });
    resolve(items);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "获取目录结构失败");
    resolve([]);
  }
}

async function refreshFsNode(node: any, data: FsNode): Promise<void> {
  await queryClient.invalidateQueries({ queryKey: ["fs-nodes", data.path] });
  if (node) {
    node.loaded = false;
    node.expand();
  }
}

async function refreshAllFsNodes(): Promise<void> {
  await queryClient.invalidateQueries({ queryKey: ["fs-nodes"] });
  if (treeRef.value?.store) {
    treeRef.value.store.root.loaded = false;
    treeRef.value.store.root.loadChildren();
  }
  ElMessage.success("目录结构与任务状态已刷新");
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

onMounted(() => {
  void load();
});

defineExpose({ dirty });
</script>

<template>
  <div v-loading="loading" class="config-block">
    <div class="head">
      <span>{{
        panel === "routes" ? "路径" : panel === "telegram" ? "Telegram" : panel === "drive" ? "Google Drive" : panel === "watch" ? "监听" : "处理"
      }}</span>
      <el-button type="primary" :loading="saving" :disabled="!dirty" @click="submit">
        保存到 upload.toml
      </el-button>
    </div>

    <el-form label-position="top" class="form">
      <section v-if="panel === 'telegram'" class="section">
        <div class="route-head">
          <span class="section-title">群与频道</span>
          <el-button size="small" @click="openAddChat">添加</el-button>
        </div>
        <p class="route-hint">从已登录的个人号里选择群或频道。名称先用官方标题，可以之后再改。</p>
        <div v-if="form.chats.length === 0" class="route-empty">还没有群。点右上角添加。</div>
        <div v-for="item in form.chats" :key="item.chat_id" class="map-card fallback">
          <div class="map-name">{{ item.alias.trim() || item.title.trim() || "未命名" }}</div>
          <div v-if="item.alias.trim() && item.title.trim()" class="map-path">官方名 {{ item.title }}</div>
          <div class="map-dest">
            <span class="map-id">{{ item.chat_id }}</span>
            <el-button size="small" text type="danger" @click="removeChat(item.chat_id)">删除</el-button>
          </div>
        </div>
        <div class="cols">
          <el-form-item label="全局话题">
            <el-switch v-model="form.topic_creation_enabled" />
          </el-form-item>
        </div>
      </section>

      <section v-if="ENABLE_GOOGLE_DRIVE && panel === 'drive'" class="section">
        <div class="route-head">
          <span class="section-title">文件夹</span>
          <el-button size="small" @click="showAddDrive = true; newDriveName = ''; newDriveId = ''">添加</el-button>
        </div>
        <p class="route-hint">手动添加要投递的 Drive 文件夹。上传客户端接入前，这里的目标可以先选，文件会停在等待。</p>
        <div v-if="form.drive_folders.length === 0" class="route-empty">还没有文件夹。</div>
        <div v-for="item in form.drive_folders" :key="item.folder_id" class="map-card fallback">
          <div class="map-name">{{ item.name.trim() || item.folder_id }}</div>
          <div class="map-dest">
            <span class="map-id">{{ item.folder_id }}</span>
            <el-button size="small" text type="danger" @click="removeDrive(item.folder_id)">删除</el-button>
          </div>
        </div>
      </section>

      <section v-if="panel === 'routes'" class="section">
        <div class="route-head">
          <span class="section-title">映射目录与文件路由</span>
          <el-button size="small" @click="refreshAllFsNodes">全部刷新</el-button>
        </div>
        <p class="route-hint">每个目录一行状态。目标、说明和封面都在「设置」里改。未命中规则的文件不会上传。</p>
        <div v-if="form.observer_paths.length === 0" class="route-empty">请先在「监听」里添加目录。</div>
        <div v-else class="fs-tree-wrapper">
          <el-tree
            ref="treeRef"
            lazy
            :load="loadFsNodes"
            node-key="path"
            :props="{ label: 'name', isLeaf: (data: FsNode) => !data.is_dir }"
            class="fs-tree"
          >
            <template #default="{ node, data }">
              <div class="tree-node-row">
                <div class="node-left" :class="{ 'is-dir': data.is_dir }">
                  <span v-if="data.is_dir" class="node-icon">📂</span>
                  <div class="node-copy">
                    <div class="node-title">
                      <span class="node-name" :class="{ 'is-root': data.is_root }" :title="data.path">
                        {{ data.name }}
                      </span>
                      <span v-if="data.is_root" class="node-badge root-badge">监控根目录</span>
                    </div>
                    <div v-if="data.is_dir" class="node-sub" :class="{ miss: routeSummary(data) === '未命中' }">
                      {{ routeSummary(data) }}
                    </div>
                  </div>
                  <template v-if="!data.is_dir">
                    <span class="file-size">{{ formatBytes(data.size || 0) }}</span>
                    <span v-if="!data.supported_ext" class="file-ext-unsupported">未监听格式</span>
                    <span v-else-if="data.task_status === 'uploading'" class="file-status status-uploading">上传中</span>
                    <span v-else-if="data.task_status === 'success'" class="file-status status-success">已上传</span>
                    <span v-else-if="data.task_status === 'pending' || data.task_status === 'preparing'" class="file-status status-pending">排队中</span>
                    <span v-else-if="data.task_status === 'failed'" class="file-status status-failed" :title="data.task_error || '失败'">失败</span>
                    <span v-else-if="data.task_status === 'unmatched'" class="file-status status-unmatched">未命中</span>
                  </template>
                </div>

                <div v-if="data.is_dir" class="node-right" @click.stop>
                  <el-button size="small" @click="openRouteSettings(data.path)">设置</el-button>
                  <el-button
                    v-if="hasExplicitRoute(data.path) && !data.is_root"
                    size="small"
                    text
                    type="danger"
                    title="清除专属规则，恢复跟随父级"
                    @click="setPathDest(data.path, '')"
                  >
                    恢复继承
                  </el-button>
                  <el-button
                    size="small"
                    text
                    class="btn-refresh"
                    title="刷新此目录"
                    @click="refreshFsNode(node, data)"
                  >
                    🔄
                  </el-button>
                </div>
              </div>
            </template>
          </el-tree>
        </div>
        <div v-if="unmatchedFiles.length" class="unmatched">
          <div class="section-title later">未命中的文件</div>
          <p class="route-hint">这些文件没有配到群或 Drive，未进入上传队列。</p>
          <div v-for="file in unmatchedFiles" :key="file.id" class="map-card fallback">
            <div class="map-name">{{ file.file_name }}</div>
            <div class="map-path">{{ file.file_path }}</div>
          </div>
        </div>
      </section>

      <section v-if="panel === 'watch'" class="section">
        <p class="route-hint">相对路径相对进程工作目录。目录必须已存在，程序不会创建。</p>
        <el-form-item label="监听目录（可配置多条路径）">
          <el-select
            v-model="form.observer_paths"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="相对或绝对路径，回车添加"
            class="grow"
          />
          <ul v-if="form.observer_path_infos.length" class="path-hints">
            <li v-for="item in form.observer_path_infos" :key="item.path">
              <span>{{ item.path }}</span>
              <span v-if="item.ok" class="ok">可用</span>
              <span v-else class="bad">{{ item.error || "目录不存在" }}</span>
            </li>
          </ul>
        </el-form-item>
        <div class="cols">
          <el-form-item label="监听文件格式">
            <el-select
              v-model="form.watch_extensions"
              multiple
              filterable
              allow-create
              default-first-option
              placeholder="留空表示任意格式；回车添加"
              class="grow"
            />
          </el-form-item>
          <el-form-item label="写稳等待（秒）">
            <el-input-number v-model="form.stable_timeout_seconds" :min="1" :step="30" />
          </el-form-item>
        </div>
      </section>

      <section v-if="panel === 'process'" class="section">
        <h3 class="section-title">封面与收尾</h3>
        <div class="cols">
          <el-form-item label="封面模式">
            <el-select v-model="form.preview" class="grow">
              <el-option label="关闭" value="off" />
              <el-option label="首帧截图" value="first_frame" />
              <el-option label="网格缩略图" value="grid" />
            </el-select>
          </el-form-item>
          <el-form-item label="默认说明" class="wide">
            <el-input
              v-model="form.caption_template"
              type="textarea"
              :rows="2"
              maxlength="2000"
              placeholder="路由都没写说明时使用。留空表示不写说明。"
            />
          </el-form-item>
          <el-form-item label="封面临时目录">
            <el-input v-model="form.page_dir" />
          </el-form-item>
          <el-form-item label="上传成功后">
            <el-select v-model="form.after_success" class="grow">
              <el-option label="保留本地文件" value="keep" />
              <el-option label="删除本地文件" value="delete" />
              <el-option label="归档到 uploaded/" value="move_to_archive" />
            </el-select>
          </el-form-item>
          <el-form-item label="过大视频">
            <el-switch v-model="form.auto_slice" active-text="自动切片" />
          </el-form-item>
          <el-form-item label="归档目录">
            <el-input v-model="form.archive_dir" />
          </el-form-item>
        </div>
        <h3 class="section-title later">并发与容错</h3>
        <div class="cols">
          <el-form-item label="最大重试次数">
            <el-input-number v-model="form.max_retries" :min="1" :max="20" />
          </el-form-item>
          <el-form-item label="上传超时（秒）">
            <el-input-number v-model="form.upload_timeout_seconds" :min="30" :step="30" />
          </el-form-item>
          <el-form-item label="抢占超时（秒）">
            <el-input-number v-model="form.assigned_timeout_seconds" :min="30" :step="30" />
          </el-form-item>
        </div>
      </section>
    </el-form>

    <el-dialog
      :model-value="stylePath !== ''"
      title="目录设置"
      width="480px"
      append-to-body
      @close="stylePath = ''"
    >
      <p class="route-hint">{{ stylePath }}</p>
      <el-form label-position="top">
        <el-form-item label="投递目标">
          <el-select v-model="styleDest" class="grow" placeholder="跟随父级">
            <el-option label="跟随父级" value="" />
            <el-option-group v-if="form.chats.length" label="Telegram">
              <el-option
                v-for="chat in form.chats"
                :key="chat.chat_id"
                :label="chatLabel(chat.chat_id)"
                :value="`tg:${chat.chat_id}`"
              />
            </el-option-group>
            <el-option-group v-if="ENABLE_GOOGLE_DRIVE && form.drive_folders.length" label="Google Drive">
              <el-option
                v-for="folder in form.drive_folders"
                :key="folder.folder_id"
                :label="folder.name.trim() || folder.folder_id"
                :value="`gd:${folder.folder_id}`"
              />
            </el-option-group>
          </el-select>
          <p v-if="!styleDest" class="route-hint">跟随父级会去掉这个目录自己的说明和封面。</p>
        </el-form-item>
        <el-form-item label="说明">
          <el-select v-model="styleCaptionMode" class="grow">
            <el-option label="跟随上一级" value="inherit" />
            <el-option label="不要说明" value="none" />
            <el-option label="自己写" value="custom" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="styleCaptionMode === 'custom'" label="说明模板">
          <el-input
            ref="captionBox"
            v-model="styleCaption"
            type="textarea"
            :rows="3"
            maxlength="2000"
            placeholder="{folder} / {stem}"
          />
          <div class="caption-chips">
            <button
              v-for="chip in CAPTION_CHIPS"
              :key="chip.token"
              type="button"
              class="caption-chip"
              :class="{ on: chipOn(chip.token) }"
              @mousedown.prevent
              @click="toggleChip(chip.token)"
            >
              {{ chip.label }}
            </button>
          </div>
          <p class="route-hint">预览　{{ stylePreviewText }}</p>
        </el-form-item>
        <el-form-item label="封面">
          <el-select v-model="stylePreview" class="grow">
            <el-option label="跟随上一级" value="inherit" />
            <el-option label="关闭" value="off" />
            <el-option label="首帧截图" value="first_frame" />
            <el-option label="网格缩略图" value="grid" />
          </el-select>
          <p class="route-hint">没写时用处理页的封面模式。一路往上都没写说明时，用处理页的默认说明。</p>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="stylePath = ''">取消</el-button>
        <el-button type="primary" @click="applyRouteSettings">保存</el-button>
      </template>
    </el-dialog>

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
.config-block {
  min-width: 0;
}

.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
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
  border-radius: 12px;
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
  border-radius: 12px;
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
  border-radius: 10px;
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
  border-radius: 14px;
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
  border-radius: 8px;
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
  align-items: flex-start;
}

.node-copy {
  min-width: 0;
}

.node-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.node-sub {
  margin-top: 2px;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.4;
  white-space: normal;
}

.node-sub.miss {
  color: #dc2626;
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
  border-radius: 999px;
  padding: 2px 9px;
  font-size: 12px;
  cursor: pointer;
}

.caption-chip.on {
  background: var(--accent-soft);
  border-color: rgba(40, 153, 90, 0.35);
  color: var(--accent);
}

.node-icon {
  font-size: 14px;
  flex-shrink: 0;
}

.node-name {
  font-size: 12.5px;
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
  font-size: 10.5px;
  padding: 1px 6px;
  border-radius: 4px;
  flex-shrink: 0;
}

.root-badge {
  background: var(--accent-soft);
  color: var(--accent);
  border: 1px solid rgba(40, 153, 90, 0.25);
  font-weight: 500;
}

.file-size {
  font-size: 11px;
  color: var(--text-secondary);
  font-family: ui-monospace, "SF Mono", monospace;
  flex-shrink: 0;
}

.file-ext-unsupported {
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 4px;
  background: #f3f4f6;
  color: #6b7280;
  border: 1px solid #e5e7eb;
  flex-shrink: 0;
}

.file-status {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 4px;
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
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 6px;
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
  font-size: 11px;
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
    border-radius: 16px;
  }
}
</style>

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
import { ElMessage } from "element-plus";
import { fetchDialogChats, fetchSettings, fetchUnmatched, saveSettings, syncDialogChats } from "../api";
import type { DialogChat } from "../api";
import type { UnmatchedFile } from "../types";
import type { UploadConfig } from "../types";

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
  topic_creation_enabled: true,
  after_success: "keep",
  concurrency: 3,
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
        name: item.name.trim(),
        path: item.path.trim(),
        chat_id: item.chat_id,
        topic_enabled: item.topic_enabled,
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
const editingPath = ref("");

function samePath(left: string, right: string): boolean {
  // 统一斜杠、去掉末尾分隔符并忽略大小写，用于匹配目录路由。
  return left.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase() === right.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

const unmatchedFiles = ref<UnmatchedFile[]>([]);

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
    editingPath.value = "";
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
      enabled: true,
      ...next,
    });
  }
  editingPath.value = "";
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

async function loadUnmatched(): Promise<void> {
  // 加载未命中路由文件，帮助用户发现尚未配置的监听目录。
  try {
    unmatchedFiles.value = await fetchUnmatched();
  } catch {
    unmatchedFiles.value = [];
  }
}

onMounted(() => {
  void load();
  void loadUnmatched();
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

      <section v-if="panel === 'drive'" class="section">
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
        <p class="route-hint">左路径、右目标名称。未指定就是未命中，文件不会上传。</p>
        <div v-if="form.observer_paths.length === 0" class="route-empty">请先在「监听」里添加目录。</div>
        <div v-for="path in form.observer_paths" :key="path" class="path-card">
          <div class="path-left">{{ path }}</div>
          <div class="path-right">
            <template v-if="editingPath === path">
              <el-select
                class="path-select"
                :model-value="pathDestKey(path)"
                size="small"
                @change="(value: string) => setPathDest(path, value)"
              >
                <el-option label="未命中" value="" />
                <el-option-group v-if="form.chats.length" label="Telegram">
                  <el-option
                    v-for="chat in form.chats"
                    :key="chat.chat_id"
                    :label="chatLabel(chat.chat_id)"
                    :value="`tg:${chat.chat_id}`"
                  />
                </el-option-group>
                <el-option-group v-if="form.drive_folders.length" label="Google Drive">
                  <el-option
                    v-for="folder in form.drive_folders"
                    :key="folder.folder_id"
                    :label="folder.name.trim() || folder.folder_id"
                    :value="`gd:${folder.folder_id}`"
                  />
                </el-option-group>
              </el-select>
            </template>
            <template v-else>
              <span class="map-id">{{ pathDestLabel(path) }}</span>
              <el-button size="small" text @click="editingPath = path">修改</el-button>
            </template>
          </div>
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
          <el-form-item label="归档目录">
            <el-input v-model="form.archive_dir" />
          </el-form-item>
        </div>
        <h3 class="section-title later">并发与容错</h3>
        <div class="cols">
          <el-form-item label="每账号并发流数">
            <el-input-number v-model="form.concurrency" :min="1" :max="32" />
          </el-form-item>
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

@media (max-width: 900px) {
  .cols {
    grid-template-columns: 1fr 1fr;
  }

  .route-row {
    grid-template-columns: 1fr 1fr;
  }
}

@media (max-width: 560px) {
  .cols {
    grid-template-columns: 1fr;
  }
}
</style>

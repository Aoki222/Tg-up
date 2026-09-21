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
import { fetchSettings, resolveChat, saveSettings } from "../api";
import type { UploadConfig } from "../types";

const { panel } = defineProps<{
  panel: "delivery" | "watch" | "process";
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
      }))
      .sort((left, right) => left.path.localeCompare(right.path)),
    chats: [...(config.chats ?? [])]
      .map((item) => ({
        chat_id: item.chat_id,
        alias: item.alias.trim(),
        title: item.title?.trim() ?? "",
      }))
      .sort((left, right) => left.chat_id - right.chat_id),
  });
}

/**
 * 将服务端返回的配置合并入本地表单，并重置脏检查基准快照
 */
function applyServer(data: UploadConfig): void {
  Object.assign(form, data, { routes: data.routes ?? [], chats: data.chats ?? [] });
  savedSnapshot.value = snapshotOf({ ...form });
}

const showAddChat = ref(false);
const newChatId = ref<number | undefined>(undefined);
const newChatName = ref("");
const addingChat = ref(false);
const editingPath = ref("");

function samePath(left: string, right: string): boolean {
  return left.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase() === right.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

function chatLabel(chatId: number): string {
  if (!chatId) return "默认群";
  const item = form.chats.find((entry) => entry.chat_id === chatId);
  if (!item) return String(chatId);
  return item.alias.trim() || item.title.trim() || String(chatId);
}

function pathChatId(path: string): number {
  const route = form.routes.find((item) => item.enabled && samePath(item.path, path));
  return route?.chat_id ?? 0;
}

function setPathChat(path: string, chatId: number): void {
  const index = form.routes.findIndex((item) => samePath(item.path, path));
  if (!chatId) {
    if (index >= 0) form.routes.splice(index, 1);
    editingPath.value = "";
    return;
  }
  if (index >= 0) {
    form.routes[index].chat_id = chatId;
    form.routes[index].enabled = true;
  } else {
    form.routes.push({
      name: "",
      path,
      chat_id: chatId,
      topic_enabled: null,
      enabled: true,
    });
  }
  editingPath.value = "";
}

function openAddChat(): void {
  newChatId.value = undefined;
  newChatName.value = "";
  showAddChat.value = true;
}

async function confirmAddChat(): Promise<void> {
  const chatId = Number(newChatId.value);
  if (!chatId) {
    ElMessage.error("请填写 chat_id");
    return;
  }
  if (form.chats.some((item) => item.chat_id === chatId)) {
    ElMessage.error("该群已在看板中");
    return;
  }
  addingChat.value = true;
  try {
    let title = "";
    try {
      title = (await resolveChat(chatId)).title || "";
    } catch {
      title = "";
    }
    form.chats.push({
      chat_id: chatId,
      alias: newChatName.value.trim(),
      title,
    });
    showAddChat.value = false;
    if (!newChatName.value.trim() && !title) {
      ElMessage.warning("未取到官方名称，名称留空");
    }
  } finally {
    addingChat.value = false;
  }
}

function removeChat(chatId: number): void {
  form.chats = form.chats.filter((item) => item.chat_id !== chatId);
  form.routes = form.routes.filter((item) => item.chat_id !== chatId);
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
      <span>{{ panel === "delivery" ? "投递" : panel === "watch" ? "监听" : "处理" }}</span>
      <el-button type="primary" :loading="saving" :disabled="!dirty" @click="submit">
        保存到 upload.toml
      </el-button>
    </div>

    <el-form label-position="top" class="form">
      <section v-if="panel === 'delivery'" class="section">
        <div class="cols">
          <el-form-item label="默认群 chat_id">
            <el-input-number v-model="form.chat_id" :controls="false" class="grow" />
          </el-form-item>
          <el-form-item label="默认创建论坛话题">
            <el-switch v-model="form.topic_creation_enabled" />
          </el-form-item>
        </div>

        <div class="route-head">
          <span class="section-title">群与频道</span>
          <el-button size="small" @click="openAddChat">添加</el-button>
        </div>
        <p class="route-hint">手动添加要投递的群或频道。名称为空时使用 Telegram 官方标题。</p>
        <div v-if="form.chats.length === 0" class="route-empty">还没有群。点右上角添加。</div>
        <div v-for="item in form.chats" :key="item.chat_id" class="map-card fallback">
          <div class="map-name">{{ item.alias.trim() || item.title.trim() || "未命名" }}</div>
          <div v-if="item.alias.trim() && item.title.trim()" class="map-path">官方名 {{ item.title }}</div>
          <div class="map-dest">
            <span class="map-id">{{ item.chat_id }}</span>
            <el-button size="small" text type="danger" @click="removeChat(item.chat_id)">删除</el-button>
          </div>
        </div>

        <div class="route-head">
          <span class="section-title">路径到群</span>
        </div>
        <p class="route-hint">左路径、右群名。点修改后从已添加的群里选；未指定则走默认群。</p>
        <div v-if="form.observer_paths.length === 0" class="route-empty">请先在「监听」里添加目录。</div>
        <div v-for="path in form.observer_paths" :key="path" class="path-card">
          <div class="path-left">{{ path }}</div>
          <div class="path-right">
            <template v-if="editingPath === path">
              <el-select
                class="path-select"
                :model-value="pathChatId(path)"
                size="small"
                @change="(value: number) => setPathChat(path, value)"
              >
                <el-option label="默认群" :value="0" />
                <el-option
                  v-for="chat in form.chats"
                  :key="chat.chat_id"
                  :label="chatLabel(chat.chat_id)"
                  :value="chat.chat_id"
                />
              </el-select>
            </template>
            <template v-else>
              <span class="map-id">{{ chatLabel(pathChatId(path)) }}</span>
              <el-button size="small" text @click="editingPath = path">修改</el-button>
            </template>
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

    <el-dialog v-model="showAddChat" title="添加群或频道" width="420px" append-to-body>
      <el-form label-position="top">
        <el-form-item label="名称">
          <el-input v-model="newChatName" placeholder="可留空，将使用官方名称" />
        </el-form-item>
        <el-form-item label="chat_id">
          <el-input-number v-model="newChatId" :controls="false" class="grow" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAddChat = false">取消</el-button>
        <el-button type="primary" :loading="addingChat" @click="confirmAddChat">添加</el-button>
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

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
import { fetchChats, fetchSettings, saveSettings } from "../api";
import type { FolderRouteItem, TelegramChat, UploadConfig } from "../types";

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
      .map((item) => ({ chat_id: item.chat_id, alias: item.alias.trim() }))
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

const selectedRoute = ref<number | null>(null);
const editorOpen = ref(false);
const liveChats = ref<TelegramChat[]>([]);
const chatsOnline = ref(false);
const chatsLoading = ref(false);

function addRoute(): void {
  form.routes.push({
    name: "",
    path: form.observer_paths[0] ?? "",
    chat_id: form.chat_id,
    topic_enabled: null,
    enabled: true,
  });
  selectedRoute.value = form.routes.length - 1;
  editorOpen.value = true;
}

function removeRoute(index: number): void {
  form.routes.splice(index, 1);
  if (selectedRoute.value === index) {
    selectedRoute.value = null;
    editorOpen.value = false;
  } else if (selectedRoute.value !== null && selectedRoute.value > index) {
    selectedRoute.value -= 1;
  }
}

function selectRoute(index: number): void {
  selectedRoute.value = index;
  editorOpen.value = true;
}

function pathGlob(path: string): string {
  const normalized = path.replace(/\\/g, "/").replace(/\/+$/, "").trim();
  return normalized ? `${normalized}/**` : "未填写路径";
}

function routeTitle(item: FolderRouteItem): string {
  const named = item.name.trim();
  if (named) return named;
  const parts = item.path.replace(/\\/g, "/").split("/").filter(Boolean);
  return parts[parts.length - 1] || "未命名规则";
}

function topicChip(item: FolderRouteItem): string {
  if (item.topic_enabled === true) return "话题开";
  if (item.topic_enabled === false) return "话题关";
  return "话题跟随";
}

const editingRoute = computed(() => {
  if (selectedRoute.value === null) return null;
  return form.routes[selectedRoute.value] ?? null;
});

const catalog = computed(() => {
  const map = new Map<number, TelegramChat>();
  for (const item of liveChats.value) {
    map.set(item.id, { id: item.id, title: item.title, alias: item.alias || "" });
  }
  for (const chat of form.chats) {
    const previous = map.get(chat.chat_id) ?? { id: chat.chat_id, title: "", alias: "" };
    previous.alias = chat.alias;
    map.set(chat.chat_id, previous);
  }
  if (form.chat_id && !map.has(form.chat_id)) {
    map.set(form.chat_id, { id: form.chat_id, title: "", alias: "" });
  }
  for (const route of form.routes) {
    if (route.chat_id && !map.has(route.chat_id)) {
      map.set(route.chat_id, { id: route.chat_id, title: "", alias: "" });
    }
  }
  return [...map.values()].sort((left, right) => left.id - right.id);
});

function chatLabel(chatId: number): string {
  const item = catalog.value.find((entry) => entry.id === chatId);
  if (!item) return String(chatId);
  return item.alias.trim() || item.title || String(chatId);
}

function setAlias(chatId: number, alias: string): void {
  const trimmed = alias.trim();
  const index = form.chats.findIndex((item) => item.chat_id === chatId);
  if (!trimmed) {
    if (index >= 0) form.chats.splice(index, 1);
    return;
  }
  if (index >= 0) form.chats[index].alias = trimmed;
  else form.chats.push({ chat_id: chatId, alias: trimmed });
}

function toggleRouteEnabled(index: number): void {
  const item = form.routes[index];
  if (item) item.enabled = !item.enabled;
}

async function loadChats(): Promise<void> {
  chatsLoading.value = true;
  try {
    const data = await fetchChats();
    liveChats.value = data.items;
    chatsOnline.value = data.online;
  } catch {
    liveChats.value = [];
    chatsOnline.value = false;
  } finally {
    chatsLoading.value = false;
  }
}

function topicMode(item: FolderRouteItem): "inherit" | "on" | "off" {
  if (item.topic_enabled === true) return "on";
  if (item.topic_enabled === false) return "off";
  return "inherit";
}

function setTopicMode(item: FolderRouteItem, mode: "inherit" | "on" | "off"): void {
  item.topic_enabled = mode === "inherit" ? null : mode === "on";
}

function setEditingTopic(mode: string): void {
  const item = editingRoute.value;
  if (!item) return;
  setTopicMode(item, mode as "inherit" | "on" | "off");
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
  void loadChats();
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
          <el-button size="small" :loading="chatsLoading" @click="loadChats">同步</el-button>
        </div>
        <p v-if="!chatsOnline" class="route-hint">没有在线账号时只显示配置里的 id。点同步可从 Session 拉取官方名称。</p>
        <div v-if="catalog.length === 0" class="route-empty">还没有群。保存默认 chat_id 或同步 Session。</div>
        <div v-for="item in catalog" :key="item.id" class="map-card fallback">
          <div class="map-name">{{ item.alias.trim() || item.title || item.id }}</div>
          <div v-if="item.title && item.alias" class="map-path">官方名 {{ item.title }}</div>
          <div class="map-dest">
            <span class="map-id">{{ item.id }}</span>
          </div>
          <el-input
            class="alias-input"
            :model-value="item.alias"
            placeholder="自定义名称，留空用官方名"
            @change="(value: string) => setAlias(item.id, value)"
          />
        </div>

        <div class="route-head">
          <span class="section-title">路径到群</span>
          <el-button size="small" @click="addRoute">添加规则</el-button>
        </div>
        <p class="route-hint">路径和目标从监听目录、上面的群列表里选。禁用或删除后该路径走默认群。</p>

        <div v-if="form.routes.length === 0" class="route-empty">没有路由规则，监听目录全部发到默认群。</div>
        <div
          v-for="(item, index) in form.routes"
          :key="index"
          class="map-card"
          :class="{ active: editorOpen && selectedRoute === index, muted: !item.enabled }"
          @click="selectRoute(index)"
        >
          <div class="map-name">{{ routeTitle(item) }}</div>
          <div class="map-path">{{ pathGlob(item.path) }}</div>
          <div class="map-dest">
            <span class="map-id">{{ chatLabel(item.chat_id) }}</span>
            <span class="map-chip">{{ item.enabled ? topicChip(item) : "已禁用 · 走默认群" }}</span>
            <el-button size="small" text @click.stop="toggleRouteEnabled(index)">
              {{ item.enabled ? "禁用" : "启用" }}
            </el-button>
            <el-button size="small" text type="danger" @click.stop="removeRoute(index)">删除</el-button>
          </div>
        </div>
        <div class="map-card fallback">
          <div class="map-name">默认（未匹配）</div>
          <div class="map-path">其余监听路径</div>
          <div class="map-dest">
            <span class="map-id">{{ chatLabel(form.chat_id) }}</span>
            <span class="map-chip">{{ form.topic_creation_enabled ? "话题开" : "话题关" }}</span>
          </div>
        </div>

        <template v-if="editorOpen && editingRoute">
          <div class="route-head">
            <span class="section-title">编辑规则</span>
          </div>
          <div class="route-row">
            <el-form-item label="备注">
              <el-input v-model="editingRoute.name" placeholder="例如 电影频道" />
            </el-form-item>
            <el-form-item label="文件夹路径">
              <el-select v-model="editingRoute.path" filterable allow-create default-first-option class="grow" placeholder="从监听目录选择">
                <el-option v-for="path in form.observer_paths" :key="path" :label="path" :value="path" />
              </el-select>
            </el-form-item>
            <el-form-item label="目标群">
              <el-select v-model="editingRoute.chat_id" filterable class="grow" placeholder="从群列表选择">
                <el-option
                  v-for="chat in catalog"
                  :key="chat.id"
                  :label="`${chat.alias.trim() || chat.title || chat.id} (${chat.id})`"
                  :value="chat.id"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="话题">
              <el-select
                :model-value="topicMode(editingRoute)"
                class="grow"
                @update:model-value="setEditingTopic"
              >
                <el-option label="跟随全局" value="inherit" />
                <el-option label="开启" value="on" />
                <el-option label="关闭" value="off" />
              </el-select>
            </el-form-item>
            <el-button
              class="route-del"
              text
              type="danger"
              @click="selectedRoute !== null && removeRoute(selectedRoute)"
            >
              删除
            </el-button>
          </div>
        </template>
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

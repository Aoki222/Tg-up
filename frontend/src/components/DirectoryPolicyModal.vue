<script setup lang="ts">
/**
 * @file DirectoryPolicyModal.vue
 * @description 路径交付策略弹窗 (Directory Policy Modal)
 *
 * 严格同步 prototype/app_interactive_prototype.html 中的 UI 视觉与操作规范：
 * 1. 【交付目标】支持跟随父级、Telegram 各群组/频道、Google Drive 文件夹；
 * 2. 【话题归属】4 分段按钮 [固定话题] [按目录新建] [不使用] [跟随全局]，选固定话题时动态拉取 Forum Topics 蓝底下拉列表；
 * 3. 【封面生成】4 分段按钮 [跟随全局] [首帧截图] [9宫格剧情图] [关闭封面]；
 * 4. 【说明模板】快捷芯片插入 {file_name} / {size} / {folder} / {stem} / #标签，支持 Telegram HTML 并提供即时消息预览卡；
 * 5. 【真实持久化】点击「保存此策略」即时调用 saveSettings 写入 upload.toml 并生效。
 */

import { computed, nextTick, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { fetchChatTopics, fetchSettings, saveSettings } from "../api";
import type { ForumTopic } from "../api";
import type {
  ChatAliasItem,
  DriveFolderItem,
  FolderRouteItem,
  PreviewMode,
  UploadConfig,
} from "../types";
import { t } from "../i18n";

const props = defineProps<{
  /** 弹窗是否可见 */
  modelValue: boolean;
  /** 当前正在配置的绝对或相对目录路径 */
  path: string;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: boolean];
  saved: [];
}>();

// ── 响应式状态 ─────────────────────────────────────────────────

const loading = ref(false);
const saving = ref(false);
const topicsLoading = ref(false);

const destKey = ref("");
const topicMode = ref<"inherit" | "fixed" | "auto" | "off">("inherit");
const topicId = ref<number | null>(null);
const preview = ref<"inherit" | "first_frame" | "grid" | "off">("inherit");
const gridLayout = ref("");
const caption = ref("");
const settleDelay = ref(0);

const chats = ref<ChatAliasItem[]>([]);
const driveFolders = ref<DriveFolderItem[]>([]);
const topics = ref<ForumTopic[]>([]);
const globalChatId = ref<number | null>(null);

const captionInputRef = ref<HTMLTextAreaElement | null>(null);

// ── 路径计算 ───────────────────────────────────────────────────

function normalizePath(p: string): string {
  return p.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

function samePath(a: string, b: string): boolean {
  return normalizePath(a) === normalizePath(b);
}

const folderName = computed(() => {
  if (!props.path) return t("policy.folder");
  const parts = props.path.replace(/\\/g, "/").split("/").filter(Boolean);
  return parts[parts.length - 1] || props.path;
});

const isTelegramDest = computed(() => {
  return destKey.value.startsWith("tg:") || (destKey.value === "" && globalChatId.value != null);
});

const activeChatId = computed<number | null>(() => {
  if (destKey.value.startsWith("tg:")) {
    const id = Number(destKey.value.slice(3));
    return Number.isFinite(id) ? id : null;
  }
  if (!destKey.value && globalChatId.value != null) {
    return globalChatId.value;
  }
  return null;
});

// ── 数据初始化与话题拉取 ───────────────────────────────────────

async function loadPolicyData(): Promise<void> {
  if (!props.path) return;
  loading.value = true;
  try {
    const config = await fetchSettings();
    chats.value = config.chats || [];
    driveFolders.value = config.drive_folders || [];
    globalChatId.value = config.chat_id || null;

    const existing = (config.routes || []).find((r) => samePath(r.path, props.path));

    if (existing) {
      if (existing.platform === "gdrive") {
        destKey.value = `gd:${existing.dest_id}`;
      } else {
        destKey.value = `tg:${existing.dest_id || existing.chat_id}`;
      }
      topicMode.value = existing.topic_mode ?? "inherit";
      topicId.value = existing.topic_id ?? null;
      preview.value = (existing.preview as any) ?? "inherit";
      gridLayout.value = existing.grid_layout ?? "";
      caption.value = existing.caption_template ?? "";
      settleDelay.value = clampSettle(existing.settle_delay_seconds);
    } else {
      destKey.value = "";
      topicMode.value = "inherit";
      topicId.value = null;
      preview.value = "inherit";
      gridLayout.value = "";
      caption.value = "";
      settleDelay.value = 0;
    }

    await loadTopicsForActiveChat();
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : t("policy.loadFailed"));
  } finally {
    loading.value = false;
  }
}

async function loadTopicsForActiveChat(): Promise<void> {
  const cId = activeChatId.value;
  topics.value = [];
  if (cId == null) return;
  topicsLoading.value = true;
  try {
    const data = await fetchChatTopics(cId, false);
    topics.value = data.forum ? data.topics : [];
  } catch {
    topics.value = [];
  } finally {
    topicsLoading.value = false;
  }
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      void loadPolicyData();
    }
  },
  { immediate: true },
);

function onDestChange(): void {
  void loadTopicsForActiveChat();
}

// ── 自定义网格图规格解析反馈 ──────────────────────────────────────────

const parsedGridInfo = computed(() => {
  const val = gridLayout.value.trim();
  if (!val) {
    return { valid: true, text: t("policy.gridEmpty"), empty: true };
  }
  const clean = val.toLowerCase().replace(/[*×,]/g, "x");
  const parts = clean.split("x");
  if (parts.length === 2) {
    const c = parseInt(parts[0].trim(), 10);
    const r = parseInt(parts[1].trim(), 10);
    if (!isNaN(c) && !isNaN(r) && c >= 1 && c <= 20 && r >= 1 && r <= 20) {
      return {
        valid: true,
        text: t("policy.gridOk", { cols: c, rows: r, frames: c * r }),
        empty: false,
      };
    }
  }
  return {
    valid: false,
    text: t("policy.gridBad"),
    empty: false,
  };
});

// ── 快捷标签切换 (Toggle 模式：已有时移除，未有时插入) ───────────────

function hasChip(token: string): boolean {
  return caption.value.includes(token);
}

function toggleChip(token: string): void {
  const current = caption.value;
  if (hasChip(token)) {
    // 已经包含：再次点击则移除该 token，并合并清理多余连续空格
    caption.value = current
      .replaceAll(token, "")
      .replace(/\s{2,}/g, " ")
      .trim();
    return;
  }

  // 未包含：在光标处或末尾插入
  const el = captionInputRef.value;
  if (!el) {
    caption.value = current ? `${current} ${token}` : token;
    return;
  }
  const start = el.selectionStart ?? current.length;
  const end = el.selectionEnd ?? start;
  const before = current.slice(0, start);
  const after = current.slice(end);
  const pad = before && !/\s$/.test(before) ? " " : "";
  caption.value = `${before}${pad}${token}${after}`;
  void nextTick(() => {
    el.focus();
    const pos = (before + pad + token).length;
    el.setSelectionRange(pos, pos);
  });
}

// ── Telegram HTML 安全预览 ─────────────────────────────────────

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

const previewHtml = computed(() => {
  const folder = folderName.value || "download";
  let template = caption.value;
  if (!template || !template.trim()) {
    return `<span class="preview-empty">${escapeHtml(t("policy.previewEmpty"))}</span>`;
  }

  template = template
    .replaceAll("{file_name}", "Episode1_Master.mkv")
    .replaceAll("{size}", "2.40 GB")
    .replaceAll("{folder}", folder)
    .replaceAll("{stem}", "Episode1_Master")
    .replaceAll("{ext}", "mkv")
    .replaceAll("{date}", new Date().toISOString().slice(0, 10))
    .replaceAll("{rel_path}", "Episode1_Master.mkv");

  let escaped = escapeHtml(template);
  escaped = escaped
    .replace(/&lt;b&gt;(.*?)&lt;\/b&gt;/gi, "<b>$1</b>")
    .replace(/&lt;strong&gt;(.*?)&lt;\/strong&gt;/gi, "<strong>$1</strong>")
    .replace(/&lt;i&gt;(.*?)&lt;\/i&gt;/gi, "<i>$1</i>")
    .replace(/&lt;em&gt;(.*?)&lt;\/em&gt;/gi, "<em>$1</em>")
    .replace(/&lt;u&gt;(.*?)&lt;\/u&gt;/gi, "<u>$1</u>")
    .replace(/&lt;s&gt;(.*?)&lt;\/s&gt;/gi, "<s>$1</s>")
    .replace(
      /&lt;code&gt;(.*?)&lt;\/code&gt;/gi,
      "<code class='preview-code'>$1</code>",
    )
    .replace(
      /&lt;pre&gt;(.*?)&lt;\/pre&gt;/gi,
      "<pre class='preview-pre'>$1</pre>",
    )
    .replace(
      /&lt;a href=&quot;(.*?)&quot;&gt;(.*?)&lt;\/a&gt;/gi,
      "<a href='$1' target='_blank' class='preview-link'>$2</a>",
    )
    .replace(/\n/g, "<br/>");

  return escaped;
});

// ── 保存策略 ───────────────────────────────────────────────────

async function onSave(): Promise<void> {
  if (saving.value) return;
  saving.value = true;
  try {
    const config = await fetchSettings();
    const targetPath = props.path.trim();

    // 过滤掉原本可能存在的同一路径规则
    const updatedRoutes = (config.routes || []).filter(
      (r) => !samePath(r.path, targetPath),
    );

    if (destKey.value) {
      const isTg = destKey.value.startsWith("tg:");
      const rawDest = destKey.value.replace(/^(tg|gd):/, "");
      const chatId = isTg ? Number(rawDest) || 0 : 0;
      const platform = isTg ? "telegram" : "gdrive";

      const newRoute: FolderRouteItem = {
        path: targetPath,
        name: folderName.value,
        enabled: true,
        platform,
        dest_id: rawDest,
        chat_id: chatId,
        topic_enabled: null,
        caption_template: caption.value.trim() ? caption.value.trim().slice(0, 2000) : null,
        preview: preview.value === "inherit" ? null : (preview.value as PreviewMode),
        grid_layout:
          preview.value === "grid" && gridLayout.value.trim()
            ? gridLayout.value.trim()
            : null,
        topic_mode:
          topicMode.value === "inherit" ? null : (topicMode.value as "off" | "auto" | "fixed"),
        topic_id: topicMode.value === "fixed" ? topicId.value : null,
        settle_delay_seconds: clampSettle(settleDelay.value),
      };
      updatedRoutes.push(newRoute);
    }

    const payload: UploadConfig = {
      ...config,
      routes: updatedRoutes,
    };

    await saveSettings(payload);
    ElMessage.success(t("policy.saved", { name: folderName.value }));
    emit("saved");
    emit("update:modelValue", false);
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : t("policy.saveFailed"));
  } finally {
    saving.value = false;
  }
}

function clampSettle(value: unknown): number {
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return 0;
  return Math.min(86400, Math.floor(parsed));
}

function onClose(): void {
  emit("update:modelValue", false);
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="modelValue"
      class="policy-overlay"
      @click.self="onClose"
    >
      <div
        class="policy-modal"
        role="dialog"
        :aria-label="t('policy.dialog', { name: folderName })"
        @click.stop
      >
        <!-- 弹窗顶栏 -->
        <header class="policy-head">
          <div class="head-info">
            <h3 class="head-title">
              <span>{{ t("policy.heading") }} </span>
              <span class="head-name">{{ folderName }}</span>
            </h3>
            <p class="head-path font-mono" :title="path">{{ path }}</p>
          </div>
          <button
            type="button"
            class="close-btn"
            :title="t('policy.close')"
            @click="onClose"
          >
            ✕
          </button>
        </header>

        <!-- 弹窗主体 -->
        <div v-loading="loading" class="policy-body">
          <!-- 1. 投递目标 (群组 / 频道) -->
          <div class="form-section">
            <label class="section-label">{{ t("policy.dest") }}</label>
            <select
              v-model="destKey"
              class="target-select"
              @change="onDestChange"
            >
              <option value="">{{ t("policy.inherit") }}</option>
              <optgroup v-if="chats.length" :label="t('policy.chats')">
                <option
                  v-for="c in chats"
                  :key="c.chat_id"
                  :value="`tg:${c.chat_id}`"
                >
                  Telegram: {{ c.alias || c.title || c.chat_id }} ({{ c.chat_id }})
                </option>
              </optgroup>
              <optgroup
                v-if="globalChatId != null && !chats.some((c) => c.chat_id === globalChatId)"
                :label="t('policy.defaultGroup')"
              >
                <option :value="`tg:${globalChatId}`">
                  {{ t("policy.defaultChat", { id: globalChatId }) }}
                </option>
              </optgroup>
              <optgroup v-if="driveFolders.length" label="Google Drive">
                <option
                  v-for="d in driveFolders"
                  :key="d.folder_id"
                  :value="`gd:${d.folder_id}`"
                >
                  Drive: {{ d.name || d.folder_id }}
                </option>
              </optgroup>
            </select>
            <p v-if="!destKey" class="field-hint">
              {{ t("policy.inheritHint") }}
            </p>

            <!-- 话题归属模式 -->
            <div v-if="isTelegramDest" class="topic-box">
              <span class="topic-sub-label">{{ t("policy.topicMode") }}</span>
              <div class="segmented-grid">
                <button
                  type="button"
                  class="seg-btn"
                  :class="{ on: topicMode === 'fixed' }"
                  @click="topicMode = 'fixed'"
                >
                  {{ t("policy.topicFixed") }}
                </button>
                <button
                  type="button"
                  class="seg-btn"
                  :class="{ on: topicMode === 'auto' }"
                  @click="topicMode = 'auto'"
                >
                  {{ t("policy.topicAuto") }}
                </button>
                <button
                  type="button"
                  class="seg-btn"
                  :class="{ on: topicMode === 'off' }"
                  @click="topicMode = 'off'"
                >
                  {{ t("policy.topicOff") }}
                </button>
                <button
                  type="button"
                  class="seg-btn"
                  :class="{ on: topicMode === 'inherit' }"
                  @click="topicMode = 'inherit'"
                >
                  {{ t("policy.topicGlobal") }}
                </button>
              </div>

              <!-- 当选中固定话题时，呈现话题选择下拉框 -->
              <div v-if="topicMode === 'fixed'" class="topic-select-wrap">
                <select
                  v-if="topics.length"
                  v-model="topicId"
                  class="topic-dropdown"
                >
                  <option :value="null">{{ t("policy.pickTopic") }}</option>
                  <option
                    v-for="topic in topics"
                    :key="topic.topic_id"
                    :value="topic.topic_id"
                  >
                    #{{ topic.title }} (Topic ID: {{ topic.topic_id }})
                  </option>
                </select>
                <p v-else-if="topicsLoading" class="topic-warn">
                  {{ t("policy.topicsLoading") }}
                </p>
                <p v-else class="topic-warn">
                  {{ t("policy.topicsEmpty") }}
                </p>
              </div>
            </div>
          </div>

          <div class="divider"></div>

          <!-- 2. 视频封面 / 缩略图生成 -->
          <div class="form-section">
            <label class="section-label">{{ t("policy.cover") }}</label>
            <div class="segmented-grid">
              <button
                type="button"
                class="seg-btn"
                :class="{ 'on-accent': preview === 'inherit' }"
                @click="preview = 'inherit'"
              >
                {{ t("policy.coverGlobal") }}
              </button>
              <button
                type="button"
                class="seg-btn"
                :class="{ 'on-accent': preview === 'first_frame' }"
                @click="preview = 'first_frame'"
              >
                {{ t("policy.coverFrame") }}
              </button>
              <button
                type="button"
                class="seg-btn"
                :class="{ 'on-accent': preview === 'grid' }"
                @click="preview = 'grid'"
              >
                {{ t("policy.coverGrid") }}
              </button>
              <button
                type="button"
                class="seg-btn"
                :class="{ 'on-accent': preview === 'off' }"
                @click="preview = 'off'"
              >
                {{ t("policy.coverOff") }}
              </button>
            </div>

            <!-- 当选择自定义网格图时展开规格配置 -->
            <div v-if="preview === 'grid'" class="grid-sub-setting">
              <div class="grid-sub-row">
                <span class="grid-sub-label">{{ t("policy.gridLabel") }}</span>
                <input
                  v-model="gridLayout"
                  type="text"
                  :placeholder="t('policy.gridPlaceholder')"
                  class="target-select grid-input font-mono"
                />
                <button
                  v-if="gridLayout"
                  type="button"
                  class="btn-clear-grid"
                  :title="t('policy.gridClearTitle')"
                  @click="gridLayout = ''"
                >
                  {{ t("policy.gridFollow") }}
                </button>
              </div>

              <!-- 快捷预设按钮 -->
              <div class="grid-presets-row">
                <span class="grid-presets-label">{{ t("policy.gridPresets") }}</span>
                <div class="grid-presets-chips">
                  <button
                    v-for="p in ['3x5', '4x3', '3x3', '4x4', '2x4', '5x2']"
                    :key="p"
                    type="button"
                    class="grid-preset-chip"
                    :class="{ active: gridLayout.toLowerCase().trim() === p }"
                    @click="gridLayout = p"
                  >
                    {{ p }}
                  </button>
                </div>
              </div>

              <!-- 实时解析反馈 -->
              <div class="grid-status-tip" :class="{ 'is-err': !parsedGridInfo.valid }">
                <span class="grid-status-dot"></span>
                <span class="grid-status-text">{{ parsedGridInfo.text }}</span>
              </div>

              <p class="field-hint">
                {{ t("policy.gridHelp") }}
              </p>
            </div>
          </div>

          <div class="divider"></div>

          <!-- 3. 落稳等待 -->
          <div class="form-section">
            <label class="section-label" for="settle-delay">{{ t("policy.settle") }}</label>
            <input
              id="settle-delay"
              v-model.number="settleDelay"
              type="number"
              min="0"
              max="86400"
              step="1"
              class="target-select settle-input"
            />
            <p class="field-hint">
              {{ t("policy.settleHint") }}
            </p>
          </div>

          <div class="divider"></div>

          <!-- 4. 消息 Caption 说明模板 -->
          <div class="form-section">
            <div class="caption-head">
              <label class="section-label">{{ t("policy.caption") }}</label>
              <span class="caption-sub">{{ t("policy.captionHtml") }}</span>
            </div>

            <!-- 快捷标签插入芯片 (Toggle 模式) -->
            <div class="chips-row">
              <button
                type="button"
                class="chip-btn font-mono"
                :class="{ active: hasChip('{file_name}') }"
                @click="toggleChip('{file_name}')"
              >
                <span v-if="hasChip('{file_name}')" class="chip-check">✓</span>
                <span>{{ t("policy.chipFile") }}</span>
              </button>
              <button
                type="button"
                class="chip-btn font-mono"
                :class="{ active: hasChip('{size}') }"
                @click="toggleChip('{size}')"
              >
                <span v-if="hasChip('{size}')" class="chip-check">✓</span>
                <span>{{ t("policy.chipSize") }}</span>
              </button>
              <button
                type="button"
                class="chip-btn font-mono"
                :class="{ active: hasChip('{folder}') }"
                @click="toggleChip('{folder}')"
              >
                <span v-if="hasChip('{folder}')" class="chip-check">✓</span>
                <span>{{ t("policy.chipFolder") }}</span>
              </button>
              <button
                type="button"
                class="chip-btn font-mono"
                :class="{ active: hasChip('{stem}') }"
                @click="toggleChip('{stem}')"
              >
                <span v-if="hasChip('{stem}')" class="chip-check">✓</span>
                <span>{{ t("policy.chipStem") }}</span>
              </button>
              <button
                type="button"
                class="chip-btn font-mono"
                :class="{ active: hasChip('{date}') }"
                @click="toggleChip('{date}')"
              >
                <span v-if="hasChip('{date}')" class="chip-check">✓</span>
                <span>{{ t("policy.chipDate") }}</span>
              </button>
              <button
                type="button"
                class="chip-btn tag-btn font-medium"
                :class="{ active: hasChip('#自动化归档') }"
                @click="toggleChip('#自动化归档')"
              >
                <span v-if="hasChip('#自动化归档')" class="chip-check">✓</span>
                <span>{{ t("policy.chipTag") }}</span>
              </button>
            </div>

            <!-- 多行模板输入框 -->
            <textarea
              ref="captionInputRef"
              v-model="caption"
              rows="3"
              :placeholder="t('policy.captionPlaceholder')"
              class="caption-textarea"
            ></textarea>

            <!-- 实时 Telegram 消息预览卡 -->
            <div class="preview-card">
              <div class="preview-title">{{ t("policy.previewTitle") }}</div>
              <div class="preview-content" v-html="previewHtml"></div>
            </div>
          </div>
        </div>

        <!-- 弹窗底栏 -->
        <footer class="policy-foot">
          <span class="foot-hint font-mono">{{ t("policy.foot") }}</span>
          <div class="foot-actions">
            <button type="button" class="cancel-btn" @click="onClose">
              {{ t("policy.cancel") }}
            </button>
            <button
              type="button"
              class="save-btn"
              :disabled="saving || loading"
              @click="onSave"
            >
              <span v-if="saving" class="spinner"></span>
              <span>{{ t("policy.save") }}</span>
            </button>
          </div>
        </footer>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.policy-overlay {
  position: fixed;
  inset: 0;
  z-index: 2200;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: rgba(18, 24, 20, 0.36);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}

.policy-modal {
  display: flex;
  flex-direction: column;
  width: min(520px, 100%);
  max-height: min(85dvh, 720px);
  background: var(--surface, #ffffff);
  border-radius: var(--radius-xl, 16px);
  border: 1px solid var(--border, rgba(0, 0, 0, 0.08));
  box-shadow: 0 24px 64px -12px rgba(18, 30, 20, 0.24);
  overflow: hidden;
}

.policy-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 20px;
  border-bottom: 1px solid var(--border-light, rgba(0, 0, 0, 0.06));
  background: var(--surface, #ffffff);
  flex-shrink: 0;
}

.head-info {
  min-width: 0;
}

.head-title {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: var(--text, #111827);
  display: flex;
  align-items: center;
  gap: 4px;
}

.head-name {
  color: var(--accent, #28995a);
}

.head-path {
  margin: 3px 0 0;
  font-size: 12.5px;
  color: var(--text-secondary, #6b7280);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 380px;
}

.close-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: var(--radius-sm, 6px);
  border: 0;
  background: transparent;
  color: var(--text-secondary, #9ca3af);
  font-size: 15px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.close-btn:hover {
  background: #f3f4f6;
  color: var(--text, #111827);
}

.policy-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.form-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.section-label {
  font-size: 13.5px;
  font-weight: 700;
  color: var(--text, #1f2937);
}

.target-select {
  width: 100%;
  box-sizing: border-box;
  padding: 8px 12px;
  border-radius: var(--radius-sm, 6px);
  border: 1px solid var(--border, rgba(0, 0, 0, 0.1));
  background: #f9fafb;
  font-size: 13px;
  color: var(--text, #111827);
  outline: none;
  transition: all 0.15s ease;
}

.target-select:focus {
  background: #ffffff;
  border-color: var(--accent, #28995a);
}

.field-hint {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--text-secondary, #9ca3af);
  line-height: 1.4;
}

.settle-input {
  max-width: 8.5rem;
}

.topic-box {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: 4px;
}

.topic-sub-label {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--text-secondary, #6b7280);
}

.segmented-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 4px;
  padding: 4px;
  background: #f3f4f6;
  border-radius: var(--radius-md, 10px);
}

.seg-btn {
  padding: 6px 8px;
  border-radius: var(--radius-sm, 6px);
  border: 0;
  background: transparent;
  color: var(--text-secondary, #6b7280);
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
  text-align: center;
  white-space: nowrap;
}

.seg-btn:hover {
  color: var(--text, #111827);
}

.seg-btn.on {
  background: #ffffff;
  color: var(--text, #111827);
  font-weight: 700;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}

.seg-btn.on-accent {
  background: #ffffff;
  color: var(--accent, #28995a);
  font-weight: 700;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}

.grid-sub-setting {
  margin-top: 10px;
  padding: 12px 14px;
  background: #f9fafb;
  border-radius: var(--radius-sm, 6px);
  border: 1px solid var(--border, rgba(0, 0, 0, 0.08));
}

.grid-sub-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.grid-sub-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text, #374151);
  white-space: nowrap;
}

.grid-input {
  flex: 1;
}

.btn-clear-grid {
  padding: 6px 12px;
  font-size: 12px;
  font-weight: 500;
  color: #4b5563;
  background: #f3f4f6;
  border: 1px solid rgba(0, 0, 0, 0.1);
  border-radius: var(--radius-sm, 6px);
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s ease;
}

.btn-clear-grid:hover {
  background: #e5e7eb;
  color: #111827;
}

.grid-presets-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
}

.grid-presets-label {
  font-size: 12px;
  color: #6b7280;
  white-space: nowrap;
}

.grid-presets-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.grid-preset-chip {
  padding: 3px 8px;
  border-radius: var(--radius-sm, 6px);
  background: #ffffff;
  border: 1px solid rgba(0, 0, 0, 0.12);
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 12px;
  color: #374151;
  cursor: pointer;
  transition: all 0.15s ease;
}

.grid-preset-chip:hover {
  background: #f3f4f6;
  border-color: rgba(40, 153, 90, 0.3);
}

.grid-preset-chip.active {
  background: rgba(40, 153, 90, 0.12);
  border-color: var(--accent, #28995a);
  color: var(--accent, #28995a);
  font-weight: 700;
}

.grid-status-tip {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  font-size: 12px;
  color: #059669;
}

.grid-status-tip.is-err {
  color: #dc2626;
}

.grid-status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  flex-shrink: 0;
}

.topic-select-wrap {
  padding-top: 4px;
}

.topic-dropdown {
  width: 100%;
  box-sizing: border-box;
  padding: 8px 12px;
  border-radius: var(--radius-sm, 6px);
  border: 1px solid rgba(59, 130, 246, 0.3);
  background: rgba(59, 130, 246, 0.04);
  color: #1d4ed8;
  font-size: 13px;
  font-weight: 500;
  outline: none;
}

.topic-warn {
  margin: 0;
  padding: 6px 10px;
  border-radius: var(--radius-sm, 6px);
  background: #f9fafb;
  font-size: 12px;
  color: var(--text-secondary, #9ca3af);
}

.divider {
  height: 1px;
  background: var(--border-light, rgba(0, 0, 0, 0.06));
}

.caption-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.caption-sub {
  font-size: 12px;
  color: var(--text-secondary, #9ca3af);
}

.chips-row {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.chip-btn {
  padding: 4px 9px;
  border-radius: var(--radius-sm, 6px);
  border: 1px solid rgba(0, 0, 0, 0.08);
  background: #f3f4f6;
  color: #374151;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.chip-btn:hover {
  background: #e5e7eb;
  color: #111827;
}

.chip-btn.active {
  background: rgba(40, 153, 90, 0.12);
  color: #28995a;
  border-color: rgba(40, 153, 90, 0.35);
  font-weight: 700;
}

.chip-btn.tag-btn {
  background: #eff6ff;
  border-color: rgba(59, 130, 246, 0.25);
  color: #2563eb;
}

.chip-btn.tag-btn:hover {
  background: #dbeafe;
}

.chip-btn.tag-btn.active {
  background: rgba(40, 153, 90, 0.15);
  border-color: #28995a;
  color: #28995a;
  font-weight: 700;
}

.chip-check {
  font-size: 11.5px;
  margin-right: 3px;
  font-weight: bold;
}

.caption-textarea {
  width: 100%;
  box-sizing: border-box;
  padding: 9px 12px;
  border-radius: var(--radius-sm, 6px);
  border: 1px solid var(--border, rgba(0, 0, 0, 0.1));
  background: #f9fafb;
  font-family: ui-monospace, "SF Mono", monospace;
  font-size: 13px;
  line-height: 1.5;
  resize: vertical;
  outline: none;
  transition: all 0.15s ease;
}

.caption-textarea:focus {
  background: #ffffff;
  border-color: var(--accent, #28995a);
}

.preview-card {
  padding: 10px 12px;
  background: rgba(40, 153, 90, 0.05);
  border: 1px solid rgba(40, 153, 90, 0.2);
  border-radius: var(--radius-md, 10px);
}

.preview-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--accent, #28995a);
  margin-bottom: 4px;
}

.preview-content {
  font-size: 13px;
  color: var(--text, #1f2937);
  line-height: 1.5;
  word-break: break-all;
}

:deep(.preview-empty) {
  color: #9ca3af;
  font-style: italic;
}

:deep(.preview-code) {
  background: rgba(0, 0, 0, 0.06);
  padding: 1px 4px;
  border-radius: var(--radius-sm, 6px);
  font-family: monospace;
}

:deep(.preview-pre) {
  background: rgba(0, 0, 0, 0.06);
  padding: 4px 6px;
  border-radius: var(--radius-sm, 6px);
  font-family: monospace;
  white-space: pre-wrap;
  margin: 4px 0;
}

:deep(.preview-link) {
  color: #2563eb;
  text-decoration: underline;
}

.policy-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  background: #f9fafb;
  border-top: 1px solid var(--border-light, rgba(0, 0, 0, 0.06));
  flex-shrink: 0;
}

.foot-hint {
  font-size: 12px;
  color: var(--text-secondary, #9ca3af);
}

.foot-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.cancel-btn {
  padding: 6px 14px;
  border-radius: var(--radius-sm, 6px);
  border: 1px solid var(--border, rgba(0, 0, 0, 0.1));
  background: #ffffff;
  color: var(--text, #374151);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.cancel-btn:hover {
  background: #f3f4f6;
}

.save-btn {
  padding: 6px 16px;
  border-radius: var(--radius-sm, 6px);
  border: 0;
  background: var(--accent, #28995a);
  color: #ffffff;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  box-shadow: 0 1px 3px rgba(40, 153, 90, 0.3);
  transition: all 0.15s ease;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.save-btn:hover:not(:disabled) {
  background: #23864f;
}

.save-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.spinner {
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
</style>

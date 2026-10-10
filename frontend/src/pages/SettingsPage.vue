<script setup lang="ts">
/**
 * @file SettingsPage.vue
 * @description 系统配置页 (Settings Page) · 优雅圆角 Craft 风格
 *
 * 核心设计：
 * 1. 200px 紧凑侧栏导航：带实时搜索过滤、未保存小黄点、活跃项目绿点、数据统计胶囊与 SettingsHub 状态；
 * 2. 局部独立平滑滚动的右侧表单区；
 * 3. 账号凭据、Telegram 目标、监听目录与处理策略完全对齐原型 rounded inset-card 规范。
 */

import { computed, ref, onMounted, watch } from "vue";
import { useRoute } from "vue-router";
import { ElMessage } from "element-plus";
import { getApiToken, saveIdentity, setApiToken } from "../api";
import ConfigPanel from "../components/ConfigPanel.vue";
import { useTelegramIdentity } from "../composables/useTelegramIdentity";
import { useUnmatchedPolling } from "../composables/useUnmatched";
import { ENABLE_GOOGLE_DRIVE } from "../features";

defineOptions({ name: "SettingsPage" });

const props = defineProps<{ embedded?: boolean }>();
const emit = defineEmits<{
  "update:dirty": [value: boolean];
  section: [id: string];
}>();

const panelRef = ref<InstanceType<typeof ConfigPanel> | null>(null);
const localSection = ref("account");
useUnmatchedPolling();

const allSections = [
  { id: "account", label: "账号与凭据" },
  { id: "telegram", label: "Telegram 目标" },
  ...(ENABLE_GOOGLE_DRIVE ? [{ id: "drive", label: "Google Drive" }] : []),
  { id: "watch", label: "监听目录" },
  { id: "routes", label: "目录文件树" },
  { id: "process", label: "处理与收尾" },
] as const;

type PanelId = "routes" | "telegram" | "drive" | "watch" | "process";

const route = useRoute();
const tomlDirty = ref(false);

const section = computed(() => {
  if (props.embedded) return localSection.value;
  const value = String(route.params.section || "account");
  return allSections.some((item) => item.id === value) ? value : "account";
});

const configPanelId = computed<PanelId>(() => {
  if (section.value === "telegram" || section.value === "watch" || section.value === "process") {
    return section.value;
  }
  if (section.value === "drive" && ENABLE_GOOGLE_DRIVE) return "drive";
  return "routes";
});

// ── 侧栏搜索过滤 ───────────────────────────────────────────────
const searchQuery = ref("");

const sectionKeywords: Record<string, string[]> = {
  account: ["账号", "凭据", "api_id", "api_hash", "token", "鉴权", "密码", "credential", "account"],
  telegram: ["telegram", "目标", "chat", "chat_id", "群组", "频道", "话题", "topic"],
  drive: ["drive", "google", "云端硬盘", "folder"],
  watch: ["监听", "目录", "observer", "path", "格式", "扩展名", "extension", "超时", "stable", "watch"],
  process: ["处理", "收尾", "封面", "缩略图", "preview", "说明", "caption", "归档", "archive", "切片", "slice", "并发", "concurrency", "重试", "retry", "超时", "timeout", "process"],
  routes: ["路由", "映射", "规则", "routes"],
};

const visibleSections = computed(() => {
  const q = searchQuery.value.trim().toLowerCase();
  if (!q) return allSections;
  return allSections.filter((item) => {
    if (item.label.toLowerCase().includes(q) || item.id.toLowerCase().includes(q)) return true;
    const kw = sectionKeywords[item.id] || [];
    return kw.some((k) => k.toLowerCase().includes(q));
  });
});

const chatsCount = computed(() => panelRef.value?.chatsCount ?? 0);
const pathsCount = computed(() => panelRef.value?.pathsCount ?? 0);

// ── 账号与凭据响应式状态 ─────────────────────────────────────────

const apiId = ref<number>(0);
const apiHash = ref("");
const apiHashMasked = ref("");
const identitySaving = ref(false);
const { refresh: refreshIdentity } = useTelegramIdentity();

const tokenInput = ref("");
const saved = ref(false);

onMounted(() => {
  tokenInput.value = getApiToken();
  void refreshIdentity().then((info) => {
    if (!info) {
      ElMessage.error("读取 Telegram 凭据失败");
      return;
    }
    apiId.value = info.api_id;
    apiHashMasked.value = info.api_hash_masked;
  });
});

async function saveCredentials(): Promise<void> {
  if (!apiId.value || apiId.value < 1) {
    ElMessage.error("API_ID 必须是正整数");
    return;
  }
  identitySaving.value = true;
  try {
    await saveIdentity({ api_id: apiId.value, api_hash: apiHash.value.trim() });
    apiHash.value = "";
    const info = await refreshIdentity();
    if (info) {
      apiId.value = info.api_id;
      apiHashMasked.value = info.api_hash_masked;
    }
    ElMessage.success("凭据已保存，当前进程已立即生效");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "保存凭据失败");
  } finally {
    identitySaving.value = false;
  }
}

function saveToken(): void {
  setApiToken(tokenInput.value);
  saved.value = true;
  ElMessage.success(tokenInput.value ? "Token 已保存，后续请求将自动携带" : "Token 已清除");
  setTimeout(() => {
    saved.value = false;
  }, 2000);
}

function clearToken(): void {
  tokenInput.value = "";
  setApiToken("");
  saved.value = true;
  ElMessage.success("Token 已清除");
  setTimeout(() => {
    saved.value = false;
  }, 2000);
}

function openRouteSettings(path: string): void {
  panelRef.value?.openRouteSettings(path);
}

function setSection(id: string): void {
  localSection.value = id;
  emit("section", id);
}

watch(tomlDirty, (value) => emit("update:dirty", value), { immediate: true });

defineExpose({
  openRouteSettings,
  setSection,
  submitToml: () => panelRef.value?.submit(),
  saving: computed(() => panelRef.value?.saving ?? false),
  dirty: tomlDirty,
});
</script>

<template>
  <div class="settings-root" :class="{ embedded }">
    <!-- 弹窗主体双栏 -->
    <div class="settings-main-split">
      <!-- 左侧分类导航 (200px 宽) -->
      <aside class="settings-sidebar">
        <div class="sidebar-top">
          <input
            v-model="searchQuery"
            type="text"
            placeholder="搜索配置项..."
            class="sidebar-search-input"
          />

          <nav class="sidebar-nav-list" aria-label="配置导航">
            <button
              v-for="item in visibleSections"
              :key="item.id"
              type="button"
              class="nav-tab-btn"
              :class="{ active: section === item.id }"
              @click="setSection(item.id)"
            >
              <span>{{ item.label }}</span>
              <span v-if="section === item.id" class="nav-active-dot"></span>
              <span
                v-else-if="item.id === 'telegram' && chatsCount > 0"
                class="nav-count-badge"
              >{{ chatsCount }}</span>
              <span
                v-else-if="item.id === 'watch' && pathsCount > 0"
                class="nav-count-badge"
              >{{ pathsCount }}</span>
              <span
                v-else-if="item.id !== 'account' && tomlDirty"
                class="nav-dirty-dot"
              ></span>
            </button>
          </nav>
        </div>

        <!-- 底部状态指示 -->
        <div class="sidebar-hub-footer">
          <div class="hub-line">
            <span class="hub-name">SettingsHub:</span>
            <span class="hub-tag">热加载生效</span>
          </div>
          <div class="hub-file-names">upload.toml · telegram.json</div>
        </div>
      </aside>

      <!-- 右侧真实配置表单区 (局部独立滚动) -->
      <main class="settings-content-pane">
        <!-- 账号与凭据 tab -->
        <div v-show="section === 'account'" class="account-pane space-y-4">
          <!-- 1. Telegram 凭据 -->
          <div class="inset-card">
            <div class="card-head">
              <h3 class="card-title">Telegram 凭据</h3>
              <p class="card-hint">
                来自 <a href="https://my.telegram.org" target="_blank" rel="noreferrer" class="link-url">my.telegram.org</a>。保存后写入 <code>data/telegram.json</code>，当前进程立即生效，不必重启。
              </p>
            </div>

            <div class="card-body">
              <div class="grid-2-cols">
                <div class="form-group">
                  <label class="form-label">API_ID</label>
                  <input
                    v-model.number="apiId"
                    type="number"
                    placeholder="纯数字应用 ID"
                    class="field-input mono"
                  />
                </div>
                <div class="form-group">
                  <label class="form-label">API_HASH</label>
                  <input
                    v-model="apiHash"
                    type="password"
                    :placeholder="apiHashMasked || '留空则不修改'"
                    class="field-input mono"
                  />
                </div>
              </div>

              <div class="card-foot-flex">
                <span class="foot-note">凭据仅存储于本地保护目录</span>
                <button
                  type="button"
                  class="save-pill-btn"
                  :disabled="identitySaving"
                  @click="saveCredentials"
                >
                  <span v-if="identitySaving" class="mini-spinner"></span>
                  <span>保存凭据</span>
                </button>
              </div>
            </div>
          </div>

          <!-- 2. 控制台鉴权 -->
          <div class="inset-card">
            <div class="card-head">
              <h3 class="card-title">控制台鉴权</h3>
              <p class="card-hint">
                用于访问受保护的 API 端点。与后端 <code>API_TOKEN</code> 环境变量保持一致，存储在浏览器 localStorage 中，留空表示不鉴权。
              </p>
            </div>

            <div class="card-body">
              <div class="form-group max-w-md">
                <label class="form-label">API Token</label>
                <input
                  v-model="tokenInput"
                  type="password"
                  placeholder="输入 API Token"
                  class="field-input mono"
                />
              </div>

              <div class="token-btn-row">
                <button type="button" class="save-pill-btn" @click="saveToken">保存 Token</button>
                <button type="button" class="cancel-pill-btn" @click="clearToken">清除</button>
                <span v-if="saved" class="saved-feedback-tag">✓ Token 已保存</span>
              </div>
            </div>
          </div>
        </div>

        <!-- 其余配置项：调用 ConfigPanel -->
        <ConfigPanel
          v-show="section !== 'account'"
          ref="panelRef"
          :panel="configPanelId"
          @update:dirty="tomlDirty = $event"
        />
      </main>
    </div>
  </div>
</template>

<style scoped>
.settings-root {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  overflow: hidden;
}

.settings-main-split {
  display: flex;
  flex: 1;
  min-height: 0;
  width: 100%;
  height: 100%;
  background: #f9fafb;
}

/* 左侧分类导航 (200px 宽) */
.settings-sidebar {
  width: 200px;
  flex-shrink: 0;
  border-right: 1px solid rgba(0, 0, 0, 0.06);
  background: #ffffff;
  padding: 12px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.sidebar-top {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.sidebar-search-input {
  width: 100%;
  box-sizing: border-box;
  padding: 6px 10px;
  font-size: 12px;
  background: #f9fafb;
  border: 1px solid rgba(0, 0, 0, 0.1);
  border-radius: var(--radius-sm, 6px);
  color: #111827;
  outline: none;
  transition: all 0.15s ease;
}

.sidebar-search-input:focus {
  background: #ffffff;
  border-color: #28995a;
}

.sidebar-nav-list {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.nav-tab-btn {
  width: 100%;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border: 0;
  border-radius: var(--radius-sm, 6px);
  background: transparent;
  font-size: 12px;
  font-weight: 500;
  color: #4b5563;
  cursor: pointer;
  transition: all 0.15s ease;
  text-align: left;
}

.nav-tab-btn:hover {
  background: #f3f4f6;
  color: #111827;
}

.nav-tab-btn.active {
  background: rgba(40, 153, 90, 0.1);
  color: #28995a;
  font-weight: 600;
}

.nav-active-dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full, 9999px);
  background: #28995a;
  flex-shrink: 0;
}

.nav-count-badge {
  padding: 1px 6px;
  border-radius: var(--radius-sm, 6px);
  background: #f3f4f6;
  color: #6b7280;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 10px;
}

.nav-dirty-dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full, 9999px);
  background: #f59e0b;
  flex-shrink: 0;
}

.sidebar-hub-footer {
  padding-top: 10px;
  border-top: 1px solid rgba(0, 0, 0, 0.06);
  font-size: 11px;
  color: #9ca3af;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.hub-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.hub-tag {
  color: #28995a;
  font-weight: 600;
}

.hub-file-names {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 10px;
  color: #9ca3af;
}

/* 右侧真实表单区 (局部独立滚动) */
.settings-content-pane {
  flex: 1;
  min-width: 0;
  padding: 20px;
  overflow-y: auto;
  background: #f9fafb;
}

.account-pane {
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
  font-size: 11px;
  color: #6b7280;
  line-height: 1.5;
}

.card-hint code {
  background: rgba(0, 0, 0, 0.04);
  padding: 1px 5px;
  border-radius: var(--radius-sm, 6px);
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 10.5px;
  color: #374151;
}

.link-url {
  color: #2563eb;
  text-decoration: underline;
}

.card-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.grid-2-cols {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.form-label {
  font-size: 11.5px;
  font-weight: 600;
  color: #374151;
}

.field-input {
  width: 100%;
  box-sizing: border-box;
  padding: 6px 10px;
  background: #f9fafb;
  border: 1px solid rgba(0, 0, 0, 0.1);
  border-radius: var(--radius-sm, 6px);
  font-size: 12px;
  color: #111827;
  outline: none;
  transition: all 0.15s ease;
}

.field-input:focus {
  background: #ffffff;
  border-color: #28995a;
}

.field-input.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}


.card-foot-flex {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 4px;
}

.foot-note {
  font-size: 11px;
  color: #9ca3af;
}

.save-pill-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  background: #28995a;
  color: #ffffff;
  font-size: 12px;
  font-weight: 600;
  border: 0;
  border-radius: var(--radius-sm, 6px);
  cursor: pointer;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
  transition: all 0.15s ease;
}

.save-pill-btn:hover:not(:disabled) {
  background: #23864f;
}

.save-pill-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.token-btn-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-top: 2px;
}

.cancel-pill-btn {
  padding: 6px 14px;
  background: #ffffff;
  border: 1px solid rgba(0, 0, 0, 0.1);
  border-radius: var(--radius-sm, 6px);
  font-size: 12px;
  font-weight: 500;
  color: #4b5563;
  cursor: pointer;
  transition: all 0.15s ease;
}

.cancel-pill-btn:hover {
  background: #f9fafb;
}

.saved-feedback-tag {
  font-size: 11px;
  color: #28995a;
  font-weight: 600;
}

.mini-spinner {
  width: 11px;
  height: 11px;
  border: 2px solid #ffffff;
  border-top-color: transparent;
  border-radius: var(--radius-full, 9999px);
  animation: spin 0.8s linear infinite;
}

.max-w-md {
  max-width: 440px;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>

<script setup lang="ts">
/**
 * @file SettingsPage.vue
 * @description 系统配置页 (Settings Page)
 *
 * 核心功能：
 * 1. 【API Token 客户端凭证维护】：
 *    - 维护存放在浏览器 localStorage 中的 API Token，并在保存时即时同步至 `api.ts` 的 Axios 拦截器；
 *    - 支持密码明文/暗文快捷切换；
 * 2. 【upload.toml 热更新控制台】：
 *    - 嵌入 `ConfigPanel`，用于热更新目标频道、监听目录、并发线程数及归档清理策略；
 * 3. 【Apple 居中黄金阅读幅宽】：
 *    - 约束在 960px 舒适视距内，避免宽屏下拉伸失调。
 */

import { ref, onMounted } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { fetchIdentity, getApiToken, restartProcess, saveIdentity, setApiToken } from "../api";
import ConfigPanel from "../components/ConfigPanel.vue";

// ── 响应式状态 ─────────────────────────────────────────────────

const apiId = ref<number>(0);
const apiHash = ref("");
const apiHashMasked = ref("");
const identitySaving = ref(false);
const showHash = ref(false);

/** 当前输入的 Token 文本值（初始从 localStorage 读取） */
const tokenInput = ref("");

/** 是否明文展示 Token 字符串 */
const showToken = ref(false);

/** 保存成功的短暂反馈提示状态 */
const saved = ref(false);

// 页面挂载时自动读取当前生效的本地 Token
onMounted(() => {
  tokenInput.value = getApiToken();
  void fetchIdentity()
    .then((info) => {
      apiId.value = info.api_id;
      apiHashMasked.value = info.api_hash_masked;
    })
    .catch((error) => {
      ElMessage.error(error instanceof Error ? error.message : "读取 Telegram 凭据失败");
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
    try {
      await ElMessageBox.confirm(
        "已写入 .env。是否立即重启进程让 API_ID / API_HASH 生效？",
        "重启进程",
        { confirmButtonText: "立即重启", cancelButtonText: "稍后手动启动", type: "warning" },
      );
    } catch {
      ElMessage.success("已保存，下次手动启动后生效");
      return;
    }
    ElMessage.success("正在重启");
    try {
      await restartProcess();
    } catch {
      // 进程退出时请求可能被掐断
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "保存凭据失败");
  } finally {
    identitySaving.value = false;
  }
}

// ── 用户操作处理 ───────────────────────────────────────────────

/** 保存并更新 Token 至 localStorage */
function saveToken(): void {
  setApiToken(tokenInput.value);
  saved.value = true;
  ElMessage.success(tokenInput.value ? "Token 已保存，后续请求将自动携带" : "Token 已清除");
  setTimeout(() => {
    saved.value = false;
  }, 2000);
}

/** 清空当前 Token */
function clearToken(): void {
  tokenInput.value = "";
  setApiToken("");
  saved.value = true;
  ElMessage.success("Token 已清除");
  setTimeout(() => {
    saved.value = false;
  }, 2000);
}
</script>

<template>
  <div class="settings">
    <!-- 页面标题与导读 -->
    <div class="page-header">
      <h2 class="page-title">系统配置</h2>
      <p class="page-desc">上传策略写入 upload.toml，保存即生效。Telegram 凭据写入 .env，需重启进程。</p>
    </div>

    <ConfigPanel />

    <el-card shadow="never" class="token-card">
      <template #header>Telegram 凭据 (.env)</template>
      <p class="hint">
        来自 <a href="https://my.telegram.org" target="_blank" rel="noreferrer">my.telegram.org</a>。
        保存后写入项目根目录 <code>.env</code>，不热更新。不改 API_HASH 请留空。
      </p>
      <div class="creds-grid">
        <el-form-item label="API_ID">
          <el-input-number v-model="apiId" :controls="false" :min="1" class="grow" />
        </el-form-item>
        <el-form-item label="API_HASH">
          <el-input
            v-model="apiHash"
            :type="showHash ? 'text' : 'password'"
            :placeholder="apiHashMasked || '留空则不修改'"
            class="token-input"
            @wheel.prevent
          >
            <template #suffix>
              <span class="toggle-eye" @click="showHash = !showHash">
                <svg v-if="showHash" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" width="16" height="16">
                  <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                  <circle cx="12" cy="12" r="3"/>
                </svg>
                <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" width="16" height="16">
                  <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/>
                  <line x1="1" y1="1" x2="23" y2="23"/>
                </svg>
              </span>
            </template>
          </el-input>
        </el-form-item>
      </div>
      <el-button type="primary" :loading="identitySaving" @click="saveCredentials">保存凭据</el-button>
    </el-card>

    <!-- ── API Token 鉴权卡片 ── -->
    <el-card shadow="never" class="token-card">
      <template #header>API Token</template>
      <p class="hint">
        用于访问受保护的 API 端点。与后端 <code>API_TOKEN</code> 环境变量保持一致，留空表示不鉴权。
      </p>

      <div class="token-row" @wheel.prevent>
        <el-input
          v-model="tokenInput"
          :type="showToken ? 'text' : 'password'"
          placeholder="输入 API Token"
          clearable
          class="token-input"
        >
          <!-- 眼睛小图标：切换明文/暗文展示 -->
          <template #suffix>
            <span class="toggle-eye" @click="showToken = !showToken">
              <svg v-if="showToken" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" width="16" height="16">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                <circle cx="12" cy="12" r="3"/>
              </svg>
              <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" width="16" height="16">
                <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/>
                <line x1="1" y1="1" x2="23" y2="23"/>
              </svg>
            </span>
          </template>
        </el-input>

        <el-button type="primary" @click="saveToken">保存</el-button>
        <el-button @click="clearToken">清除</el-button>
      </div>

      <transition name="fade">
        <p v-if="saved" class="saved-hint">✓ 已保存生效</p>
      </transition>
    </el-card>

  </div>
</template>

<style scoped>
.settings {
  max-width: 1080px;
  width: 100%;
  margin: 0 auto;
  padding: 8px 0 48px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  height: 100%;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
}

.page-header {
  margin-bottom: 4px;
}

.page-title {
  margin: 0 0 4px;
  font-size: 20px;
  font-weight: 700;
  color: var(--text);
  letter-spacing: -0.02em;
}

.page-desc {
  margin: 0;
  font-size: 13px;
  color: var(--text-secondary);
}

.token-card .hint {
  margin: 0 0 14px;
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.token-card .hint code {
  padding: 2px 6px;
  background: var(--accent-soft);
  border-radius: 5px;
  font-size: 12px;
  color: var(--accent);
}

.creds-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 16px;
  margin-bottom: 12px;
}

.creds-grid :deep(.el-form-item) {
  margin-bottom: 12px;
}

.creds-grid :deep(.el-input-number) {
  width: 100%;
}

.grow {
  width: 100%;
}

.token-row {
  display: flex;
  gap: 8px;
  align-items: center;
  min-width: 0;
  overflow: hidden;
}

.token-input {
  flex: 1;
  min-width: 0;
  max-width: 480px;
  overflow: hidden;
}

.token-input :deep(.el-input__wrapper) {
  overflow: hidden;
}

.token-input :deep(.el-input__inner) {
  overflow: hidden;
  text-overflow: ellipsis;
  overscroll-behavior: none;
}

.toggle-eye {
  cursor: pointer;
  display: inline-flex;
  color: var(--text-secondary);
  transition: color 0.15s;
}

.toggle-eye:hover {
  color: var(--text);
}

.saved-hint {
  margin: 10px 0 0;
  color: var(--ok);
  font-size: 13px;
  font-weight: 500;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

@media (max-width: 860px) {
  .settings {
    padding: 12px 14px 40px;
  }

  .creds-grid {
    grid-template-columns: 1fr;
  }
}
</style>

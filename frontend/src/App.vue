<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRoute } from "vue-router";
import { fetchSystemVersion } from "./api";
import { useTelegramIdentity } from "./composables/useTelegramIdentity";
import type { SystemVersionInfo } from "./types";

const route = useRoute();
const { configured, refresh } = useTelegramIdentity();

const versionInfo = ref<SystemVersionInfo>({
  current_version: "",
  remote_version: null,
  has_update: false,
  channel: "latest",
  status: "latest",
  commit_message: "",
  commit_url: "",
});
const versionReady = ref(false);
let versionTimer = 0;

const versionLabel = computed(() => {
  if (versionInfo.value.status === "update") return "有更新";
  if (versionInfo.value.status === "dev") return "开发";
  return versionInfo.value.current_version;
});

const versionAria = computed(() => {
  if (versionInfo.value.status === "update") {
    const line = versionInfo.value.channel === "staging" ? "测试版" : "正式版";
    return `${line}有更新，远端 ${versionInfo.value.remote_version || ""}`;
  }
  if (versionInfo.value.status === "dev") return "本地开发";
  if (versionInfo.value.status === "staging") return `测试版 ${versionInfo.value.current_version}`;
  return `正式版 ${versionInfo.value.current_version}`;
});

async function loadVersion(): Promise<void> {
  try {
    versionInfo.value = await fetchSystemVersion();
    versionReady.value = true;
  } catch {
    // 保留上一次的结果。第一次失败则不显示。
  }
}

onMounted(() => {
  void refresh();
  void loadVersion();
  versionTimer = window.setInterval(() => void loadVersion(), 15 * 60 * 1000);
});
onUnmounted(() => {
  window.clearInterval(versionTimer);
});
/**
 * @file App.vue
 * @description 应用根外壳组件 (Shell Component)
 *
 * 采用方案 B（Apple / Craft 润白精密工作台）设计哲学：
 * 1. 废除传统 180px 侧边栏，消灭空间闲置，释放 100% 横向视野；
 * 2. 顶部提供居中吸顶的磨砂玻璃灵动药丸（Floating Island Pill）导引中枢；
 * 3. 采用分段药丸按钮无缝切换「监控控制台」与「系统配置」；
 * 4. 页面主体限定在 max-w-[1360px] 黄金视域内居中展开。
 */
</script>

<template>
  <div class="app-shell">
    <!-- ── 顶部吸顶居中悬浮灵动岛 (Floating Island Navigation) ── -->
    <header class="island-wrapper">
      <div class="floating-island">
        <!-- 品牌徽标 -->
        <div class="brand">
          <span class="sparkle">✦</span>
          <span class="brand-name">Uploader</span>
        </div>

        <div class="nav-divider"></div>

        <!-- 药丸式分段路由切换器 -->
        <nav class="pill-nav">
          <router-link to="/" exact-active-class="active" class="pill-item">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14">
              <rect x="3" y="3" width="7" height="7" rx="1.5"/>
              <rect x="14" y="3" width="7" height="7" rx="1.5"/>
              <rect x="3" y="14" width="7" height="7" rx="1.5"/>
              <rect x="14" y="14" width="7" height="7" rx="1.5"/>
            </svg>
            <span>监控</span>
          </router-link>
          <router-link
            to="/settings/routes"
            class="pill-item"
            :class="{ active: route.path.startsWith('/settings') }"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14">
              <circle cx="12" cy="12" r="3"/>
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
            </svg>
            <span>设置</span>
          </router-link>
        </nav>

        <div class="nav-divider"></div>

        <!-- 系统常驻在线心跳状态指示灯 -->
        <div class="system-status">
          <span class="pulse-dot"></span>
          <span class="status-label">在线</span>
        </div>

        <template v-if="versionReady">
          <div class="nav-divider version-divider"></div>
          <div class="version-section">
            <el-tooltip effect="dark" placement="bottom" :show-after="200">
              <template #content>
                <div class="version-tooltip-content">
                  <template v-if="versionInfo.status === 'update'">
                    <div class="version-tooltip-title">
                      {{ versionInfo.channel === "staging" ? "测试版有更新" : "正式版有更新" }}
                    </div>
                    <div class="version-tooltip-sub">
                      正在运行 {{ versionInfo.current_version }}，远端 {{ versionInfo.remote_version }}
                    </div>
                    <div v-if="versionInfo.commit_message" class="version-tooltip-msg">
                      {{ versionInfo.commit_message }}
                    </div>
                    <div class="version-tooltip-sub">
                      拉取 {{ versionInfo.channel === "staging" ? "staging" : "latest" }} 镜像后重新启动
                    </div>
                    <a
                      v-if="versionInfo.commit_url"
                      :href="versionInfo.commit_url"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="version-tooltip-link"
                    >
                      在 GitHub 查看
                    </a>
                  </template>
                  <template v-else-if="versionInfo.status === 'staging'">
                    <div class="version-tooltip-title">测试版</div>
                    <div class="version-tooltip-sub">当前提交 {{ versionInfo.current_version }}</div>
                  </template>
                  <template v-else-if="versionInfo.status === 'dev'">
                    <div class="version-tooltip-title">本地开发</div>
                    <div class="version-tooltip-sub">未写入镜像提交号，不检查更新</div>
                  </template>
                  <template v-else>
                    <div class="version-tooltip-title">正式版</div>
                    <div class="version-tooltip-sub">当前提交 {{ versionInfo.current_version }}</div>
                  </template>
                </div>
              </template>
              <div :class="['version-tag', `tag-${versionInfo.status}`]" role="status" :aria-label="versionAria">
                <span :class="['version-dot', `dot-${versionInfo.status}`]"></span>
                <span class="version-text">{{ versionLabel }}</span>
              </div>
            </el-tooltip>
          </div>
        </template>
      </div>
    </header>

    <p v-if="configured === false" class="setup-banner">
      欢迎使用！请先配置 API_ID / API_HASH 以激活传输服务
      <router-link to="/settings/account">去配置</router-link>
    </p>

    <!-- ── 居中通透大画幅主视口 ── -->
    <main class="main-viewport">
      <router-view v-slot="{ Component }">
        <keep-alive :include="['MonitorPage', 'SettingsPage']">
          <component :is="Component" />
        </keep-alive>
      </router-view>
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  height: 100dvh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

/* ── 顶部吸顶悬浮灵动岛 ── */
.island-wrapper {
  flex-shrink: 0;
  z-index: 1000;
  display: flex;
  justify-content: center;
  padding: 14px 16px 0;
  pointer-events: none; /* 穿透空白处点击 */
}

.floating-island {
  pointer-events: auto; /* 仅激活药丸区域点击 */
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 14px 6px 18px;
  background: rgba(255, 255, 255, 0.88);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 9999px;
  box-shadow: var(--shadow-island);
  transition: transform 0.2s cubic-bezier(0.16, 1, 0.3, 1), box-shadow 0.2s;
}

.floating-island:hover {
  border-color: rgba(0, 0, 0, 0.12);
}

.setup-banner {
  flex-shrink: 0;
  margin: 10px 24px 0;
  padding: 8px 14px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.88);
  border: 1px solid rgba(0, 0, 0, 0.06);
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.5;
  text-align: center;
}

.setup-banner a {
  margin-left: 8px;
  color: var(--accent);
  font-weight: 600;
  text-decoration: none;
}

.brand {
  display: flex;
  align-items: center;
  gap: 6px;
  user-select: none;
}

.sparkle {
  color: var(--accent);
  font-size: 14px;
  line-height: 1;
}

.brand-name {
  font-size: 14px;
  font-weight: 700;
  color: var(--text);
  letter-spacing: -0.02em;
}

.nav-divider {
  width: 1px;
  height: 16px;
  background: var(--border);
}

.pill-nav {
  display: flex;
  background: #f0f4f1;
  padding: 3px;
  border-radius: 9999px;
  gap: 2px;
}

.pill-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 14px;
  border-radius: 9999px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  text-decoration: none;
  transition: color 0.15s, background-color 0.15s, transform 0.15s cubic-bezier(0.32, 0.72, 0, 1);
}

.pill-item:hover {
  color: var(--text);
}

.pill-item:active {
  transform: scale(0.96);
}

.pill-item.active {
  color: #1a2e20;
  background: #ffffff;
  font-weight: 600;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
}

.system-status {
  display: flex;
  align-items: center;
  gap: 6px;
  padding-right: 4px;
  font-size: 12px;
  color: var(--text-secondary);
  font-weight: 500;
  user-select: none;
}

.pulse-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  animation: pulse-glow 2.2s infinite ease-in-out;
}

.version-section {
  display: flex;
  align-items: center;
  user-select: none;
}

.version-tag {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
  color: var(--text-secondary);
  background: rgba(0, 0, 0, 0.04);
  padding: 2px 7px;
  border-radius: 6px;
  letter-spacing: 0.3px;
  font-weight: 500;
  cursor: pointer;
  transition: color 0.15s, background-color 0.15s;
}

.version-tag:hover {
  background: rgba(0, 0, 0, 0.07);
}

.version-tag.tag-update {
  color: #b91c1c;
  background: rgba(239, 68, 68, 0.09);
}

.version-tag.tag-update:hover {
  background: rgba(239, 68, 68, 0.14);
}

.version-tag.tag-staging {
  color: #b45309;
  background: rgba(245, 158, 11, 0.09);
}

.version-tag.tag-staging:hover {
  background: rgba(245, 158, 11, 0.14);
}

.version-tag.tag-latest {
  color: #047857;
  background: rgba(16, 185, 129, 0.09);
}

.version-tag.tag-latest:hover {
  background: rgba(16, 185, 129, 0.14);
}

.version-tag.tag-dev {
  color: var(--text-secondary);
  background: rgba(0, 0, 0, 0.04);
}

.version-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
  transition: background-color 0.2s, box-shadow 0.2s;
}

.version-dot.dot-latest {
  background: #10b981;
  box-shadow: 0 0 5px rgba(16, 185, 129, 0.5);
}

.version-dot.dot-staging {
  background: #f59e0b;
  box-shadow: 0 0 5px rgba(245, 158, 11, 0.5);
}

.version-dot.dot-update {
  background: #ef4444;
  box-shadow: 0 0 6px rgba(239, 68, 68, 0.6);
  animation: pulse-update 2s infinite ease-in-out;
}

.version-dot.dot-dev {
  background: #9ca3af;
}

@keyframes pulse-update {
  0%, 100% {
    transform: scale(1);
    opacity: 1;
    box-shadow: 0 0 5px rgba(239, 68, 68, 0.6);
  }
  50% {
    transform: scale(1.15);
    opacity: 0.85;
    box-shadow: 0 0 9px rgba(239, 68, 68, 0.9);
  }
}

/* ── 居中大画幅主视口 ── */
.main-viewport {
  width: 100%;
  max-width: 1520px;
  margin: 0 auto;
  padding: 10px 24px 16px;
  flex: 1;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

@media (max-width: 768px) {
  .app-shell {
    height: auto;
    min-height: 100dvh;
    overflow: visible;
  }

  .island-wrapper {
    padding: max(8px, env(safe-area-inset-top)) 10px 0;
  }

  .floating-island {
    padding: 4px 10px;
    gap: 8px;
    max-width: 100%;
  }

  .system-status {
    display: none;
  }

  .version-section {
    display: flex;
  }

  .version-tag {
    padding: 2px 6px;
  }

  .pill-item {
    padding: 4px 10px;
    font-size: 12.5px;
  }

  .main-viewport {
    padding: 8px 10px max(16px, env(safe-area-inset-bottom));
    overflow: visible;
    height: auto;
  }
}
</style>

<style>
/* ── 全局弹窗样式：优化为 Apple 浮雕磨砂质感 ── */
.session-overlay {
  position: fixed;
  inset: 0;
  z-index: 2000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(22, 34, 25, 0.28);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
}

.session-modal {
  width: min(560px, 100%);
  max-height: min(90vh, 840px);
  overflow: auto;
  border-radius: 20px;
  box-shadow: 0 24px 64px -12px rgba(18, 30, 20, 0.24), inset 0 1px 0 rgba(255, 255, 255, 0.8);
}

@media (max-width: 768px) {
  .session-overlay {
    align-items: flex-end;
    padding: 0;
  }

  .session-modal {
    width: 100%;
    max-height: 90vh;
    border-radius: 20px 20px 0 0;
    box-shadow: 0 -8px 32px rgba(18, 30, 20, 0.16);
    padding-bottom: max(16px, env(safe-area-inset-bottom));
  }
}

/* ── 统一版本更新浮层样式 ── */
.version-tooltip-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-width: 260px;
  padding: 2px;
}

.version-tooltip-title {
  font-weight: 600;
  color: #ffffff;
  font-size: 12px;
}

.version-tooltip-sub {
  font-size: 11px;
  color: #9ca3af;
  line-height: 1.3;
}

.version-tooltip-msg {
  font-size: 11px;
  color: #d1d5db;
  line-height: 1.4;
  word-break: break-word;
}

.version-tooltip-link {
  font-size: 11px;
  color: #60a5fa;
  text-decoration: none;
  margin-top: 4px;
  display: inline-block;
}

.version-tooltip-link:hover {
  text-decoration: underline;
}
</style>

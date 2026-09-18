<script setup lang="ts">
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
          <router-link to="/settings" active-class="active" class="pill-item">
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
      </div>
    </header>

    <!-- ── 居中通透大画幅主视口 ── -->
    <main class="main-viewport">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

/* ── 顶部吸顶悬浮灵动岛 ── */
.island-wrapper {
  position: sticky;
  top: 14px;
  z-index: 1000;
  display: flex;
  justify-content: center;
  padding: 0 16px;
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

/* ── 居中大画幅主视口 ── */
.main-viewport {
  width: 100%;
  max-width: 1520px;
  margin: 0 auto;
  padding: 10px 24px 60px;
  flex: 1;
}

@media (max-width: 640px) {
  .floating-island {
    padding: 5px 10px;
    gap: 8px;
  }
  .system-status {
    display: none;
  }
  .main-viewport {
    padding: 12px 14px 40px;
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
</style>

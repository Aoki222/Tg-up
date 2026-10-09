<script setup lang="ts">
/**
 * 整页外壳。监控台自己放顶栏、目录坞和设置层，这里只占满视口。
 */
</script>

<template>
  <div class="app-shell">
    <main class="main-viewport">
      <router-view v-slot="{ Component }">
        <keep-alive include="MonitorPage">
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

.main-viewport {
  width: 100%;
  flex: 1;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
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

<script setup lang="ts">
import { ref, watch } from "vue";
import WorkerPanel from "./components/WorkerPanel.vue";
import ProgressPanel from "./components/ProgressPanel.vue";
import SessionPanel from "./components/SessionPanel.vue";
import ConfigPanel from "./components/ConfigPanel.vue";

const showSessionForm = ref(false);

function openSessionForm(): void {
  showSessionForm.value = true;
}

function closeSessionForm(): void {
  showSessionForm.value = false;
}

watch(showSessionForm, (open) => {
  document.body.style.overflow = open ? "hidden" : "";
});
</script>

<template>
  <div class="shell">
    <header class="hero">
      <div>
        <p class="eyebrow">Telegram Uploader</p>
        <h1>控制台</h1>
        <p class="sub">Worker、上传进度、创建 Session，以及可热更新的 upload.toml。</p>
      </div>
    </header>
    <main class="layout">
      <WorkerPanel @add="openSessionForm" />
      <ProgressPanel />
      <ConfigPanel class="span-2" />
    </main>
  </div>
  <Teleport to="body">
    <div
      v-if="showSessionForm"
      class="session-overlay"
      @click.self="closeSessionForm"
    >
      <div class="session-modal" @click.stop>
        <SessionPanel @close="closeSessionForm" />
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.shell {
  max-width: 1180px;
  margin: 0 auto;
  padding: 28px 20px 48px;
}
.hero {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  margin-bottom: 22px;
}
.eyebrow {
  margin: 0 0 6px;
  color: var(--accent);
  font-size: 12px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  font-weight: 700;
}
h1 {
  margin: 0 0 6px;
  font-size: 32px;
  letter-spacing: -0.03em;
}
.sub {
  margin: 0;
  color: var(--muted);
}
.layout {
  display: grid;
  grid-template-columns: minmax(280px, 340px) 1fr;
  gap: 16px;
  align-items: stretch;
}
.span-2 {
  grid-column: 1 / -1;
}
@media (max-width: 860px) {
  .layout {
    grid-template-columns: 1fr;
  }
  .span-2 {
    grid-column: auto;
  }
}
</style>

<style>
.session-overlay {
  position: fixed;
  inset: 0;
  z-index: 2000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(28, 43, 58, 0.32);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
}
.session-modal {
  width: min(560px, 100%);
  max-height: min(90vh, 840px);
  overflow: auto;
  border-radius: 16px;
  box-shadow: 0 24px 64px rgba(28, 43, 58, 0.22);
}
</style>

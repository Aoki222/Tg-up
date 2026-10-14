<script setup lang="ts">
import type { SystemVersionInfo } from "../types";
import { locale, setLocale, t } from "../i18n";

defineProps<{
  dockOpen: boolean;
  versionLabel: string;
  versionInfo: SystemVersionInfo | null;
  versionTitle: string;
  liveSpeed: string;
  activeWorkers: number;
  totalWorkers: number;
  successScope: "today" | "all";
  successCount: number;
  successAllTotal: number;
  settingsDirty: boolean;
}>();

const emit = defineEmits<{
  "toggle-dock": [];
  "open-version": [];
  "open-workers": [];
  "open-success": [];
  "toggle-scope": [];
  "open-settings": [];
}>();
</script>

<template>
  <header class="work-header">
    <div class="header-lead">
      <button
        type="button"
        class="sidebar-toggle-btn"
        :aria-expanded="dockOpen"
        :title="t('header.toggleDock')"
        @click="emit('toggle-dock')"
      >
        <svg class="toggle-icon" :class="{ rotated: !dockOpen }" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
        </svg>
      </button>

      <strong class="brand">Tg-up</strong>

      <button
        v-if="versionLabel"
        type="button"
        class="version-pill"
        :class="versionInfo ? `tag-${versionInfo.status}` : 'tag-latest'"
        :title="versionTitle || t('header.versionFallback')"
        @click="emit('open-version')"
      >
        <span class="version-dot"></span>
        <span>{{ versionLabel }}</span>
      </button>
    </div>

    <div class="telemetry-island">
      <div class="telemetry-badge" :title="t('header.liveTitle')">
        <svg class="telemetry-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8">
          <path d="M8.5 1.5L2.5 9h5l-1 5.5 6-7.5h-5l1-5.5z" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <span class="telemetry-label">{{ t("header.live") }}</span>
        <b class="telemetry-val font-mono">{{ liveSpeed }}</b>
      </div>
    </div>

    <div class="header-action-island">
      <button
        type="button"
        class="worker-chip-btn"
        :class="{ 'has-offline': activeWorkers < totalWorkers }"
        :title="t('header.workersTitle')"
        @click="emit('open-workers')"
      >
        <span class="worker-chip-icon">
          <svg viewBox="0 0 16 16" fill="currentColor" width="13" height="13">
            <path d="M1.5 3A1.5 1.5 0 0 1 3 1.5h10A1.5 1.5 0 0 1 14.5 3v2A1.5 1.5 0 0 1 13 6.5H3A1.5 1.5 0 0 1 1.5 5V3zm0 6A1.5 1.5 0 0 1 3 7.5h10A1.5 1.5 0 0 1 14.5 9v2a1.5 1.5 0 0 1-1.5 1.5H3A1.5 1.5 0 0 1 1.5 11V9zM3 2.5a.5.5 0 0 0-.5.5v2a.5.5 0 0 0 .5.5h10a.5.5 0 0 0 .5-.5V3a.5.5 0 0 0-.5-.5H3zm0 6a.5.5 0 0 0-.5.5v2a.5.5 0 0 0 .5.5h10a.5.5 0 0 0 .5-.5V9a.5.5 0 0 0-.5-.5H3z"/>
            <circle cx="12" cy="4" r=".75" fill="currentColor"/>
            <circle cx="12" cy="10" r=".75" fill="currentColor"/>
          </svg>
        </span>
        <span class="worker-chip-status-dot"></span>
        <span class="worker-chip-label">{{ t("header.workers") }}</span>
        <b class="worker-chip-count font-mono">{{ activeWorkers }}/{{ totalWorkers }}</b>
        <span class="worker-chip-action-pill">{{ t("header.manage") }}</span>
      </button>

      <div class="scope-island-pill">
        <button type="button" class="scope-island-view-btn" :title="t('header.historyTitle')" @click="emit('open-success')">
          <svg class="scope-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6">
            <path d="M2.5 4.5h11M2.5 8h7M2.5 11.5h5" stroke-linecap="round"/>
          </svg>
          <span class="scope-island-label">{{ successScope === "today" ? t("header.todaySent") : t("header.allSent") }}</span>
          <b class="scope-island-count font-mono">{{ successScope === "today" ? successCount : (successAllTotal || successCount) }}</b>
        </button>
        <span class="scope-island-divider"></span>
        <button type="button" class="scope-island-toggle-btn" :title="t('header.scopeTitle')" @click.stop="emit('toggle-scope')">
          {{ successScope === "today" ? t("header.scopeAll") : t("header.scopeToday") }}
        </button>
      </div>

      <div class="lang-switch" role="group" :aria-label="t('header.language')">
        <button type="button" :class="{ on: locale === 'en' }" :aria-pressed="locale === 'en'" @click="setLocale('en')">EN</button>
        <button type="button" :class="{ on: locale === 'zh' }" :aria-pressed="locale === 'zh'" @click="setLocale('zh')">中文</button>
      </div>

      <button type="button" class="settings-pill-btn" :title="t('header.settingsTitle')" @click="emit('open-settings')">
        <svg class="settings-icon" viewBox="0 0 16 16" fill="currentColor" width="14" height="14">
          <path d="M8 4.754a3.246 3.246 0 1 0 0 6.492 3.246 3.246 0 0 0 0-6.492zM5.754 8a2.246 2.246 0 1 1 4.492 0 2.246 2.246 0 0 1-4.492 0z"/>
          <path d="M9.796 1.343c-.527-1.79-3.065-1.79-3.592 0l-.094.319a.873.873 0 0 1-1.255.52l-.292-.16c-1.64-.892-3.433.902-2.54 2.541l.159.292a.873.873 0 0 1-.52 1.255l-.319.094c-1.79.527-1.79 3.065 0 3.592l.319.094a.873.873 0 0 1 .52 1.255l-.16.292c-.892 1.64.901 3.434 2.541 2.54l.292-.159a.873.873 0 0 1 1.255.52l.094.319c.527 1.79 3.065 1.79 3.592 0l.094-.319a.873.873 0 0 1 1.255-.52l.292.16c1.64.893 3.434-.902 2.54-2.541l-.159-.292a.873.873 0 0 1 .52-1.255l.319-.094c1.79-.527 1.79-3.065 0-3.592l-.319-.094a.873.873 0 0 1-.52-1.255l.16-.292c.893-1.64-.902-3.433-2.541-2.54l-.292.159a.873.873 0 0 1-1.255-.52l-.094-.319z"/>
        </svg>
        <span>{{ t("header.settings") }}</span>
        <span v-if="settingsDirty" class="dirty-dot" :aria-label="t('header.dirty')"></span>
      </button>
    </div>
  </header>
</template>

<style scoped src="./WorkHeader.css"></style>

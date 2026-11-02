<script setup lang="ts">
/**
 * 控制台登录页。未登录或仍须修改初始密码时只停在这一页。
 */
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import ConsoleAuthCard from "../components/ConsoleAuthCard.vue";
import SessionPanel from "../components/SessionPanel.vue";
import { fetchAuthStatus, hasTelegramUser, type AuthStatus } from "../api";
import { locale, setLocale, t } from "../i18n";

defineOptions({ name: "LoginPage" });

const router = useRouter();
const status = ref<AuthStatus | null>(null);
const needsTelegram = ref(false);

function signedIn(value: AuthStatus | null): boolean {
  return Boolean(value?.authenticated && !value.must_change_password);
}

async function load(): Promise<void> {
  try {
    status.value = await fetchAuthStatus();
  } catch {
    status.value = {
      owner_exists: true,
      authenticated: false,
      username: null,
      must_change_password: false,
    };
  }
  if (!signedIn(status.value)) {
    needsTelegram.value = false;
    return;
  }
  if (await hasTelegramUser()) {
    needsTelegram.value = false;
    await router.replace({ name: "monitor" });
    return;
  }
  needsTelegram.value = true;
}

onMounted(() => {
  void load();
});

async function onDone(): Promise<void> {
  await load();
}
</script>

<template>
  <div class="login-page">
    <header class="login-top">
      <strong class="brand">Tg-up</strong>
      <div class="lang-switch" role="group" :aria-label="t('header.language')">
        <button type="button" :class="{ on: locale === 'en' }" :aria-pressed="locale === 'en'" @click="setLocale('en')">EN</button>
        <button type="button" :class="{ on: locale === 'zh' }" :aria-pressed="locale === 'zh'" @click="setLocale('zh')">中文</button>
      </div>
    </header>
    <main class="login-main">
      <SessionPanel v-if="needsTelegram" forced-qr @done="onDone" />
      <ConsoleAuthCard v-else-if="status" :status="status" @done="onDone" />
    </main>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  min-height: 100dvh;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--bg, #f5f7f6);
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}

.login-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 28px 0;
}

.brand {
  font-size: 18px;
  letter-spacing: -0.03em;
}

.lang-switch {
  display: inline-flex;
  align-items: center;
  height: 32px;
  padding: 2px;
  background: #ffffff;
  border: 1px solid var(--border, rgba(0, 0, 0, 0.12));
  border-radius: var(--radius-sm, 8px);
}

.lang-switch button {
  border: 0;
  background: transparent;
  height: 26px;
  padding: 0 8px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 700;
  color: var(--text-secondary);
  cursor: pointer;
}

.lang-switch button.on {
  background: #f0fdf4;
  color: #166534;
}

.login-main {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}

.login-main :deep(.auth-card),
.login-main :deep(.session-card) {
  width: min(440px, 100%);
}

@media (max-width: 640px) {
  .login-top {
    padding: 16px 20px 0;
  }
  .login-main {
    padding: 16px;
    align-items: flex-start;
    padding-top: 28px;
  }
}
</style>

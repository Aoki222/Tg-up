/**
 * @file router.ts
 * @description 前端单页应用 (SPA) 路由配置中心
 *
 * `/` 是工作台。`/settings` 会回到 `/?settings=`，由工作台打开设置层。
 */

import { createRouter, createWebHistory } from "vue-router";
import { authRequired, fetchAuthStatus, hasTelegramUser } from "./api";
import LoginPage from "./pages/LoginPage.vue";
import MonitorPage from "./pages/MonitorPage.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/login",
      name: "login",
      component: LoginPage,
    },
    {
      path: "/",
      name: "monitor",
      component: MonitorPage,
      meta: { title: "监控控制台" },
    },
    {
      path: "/settings",
      redirect: "/?settings=telegram",
    },
    {
      path: "/settings/:section",
      redirect: (to) => ({ path: "/", query: { settings: String(to.params.section || "telegram") } }),
    },
  ],
});

function consoleReady(status: { authenticated: boolean; must_change_password: boolean } | null): boolean {
  return Boolean(status?.authenticated && !status.must_change_password);
}

router.beforeEach(async (to) => {
  let status = null;
  try {
    status = await fetchAuthStatus();
  } catch {
    status = null;
  }
  const ready = consoleReady(status);
  authRequired.value = !ready;
  const userReady = ready ? await hasTelegramUser() : false;
  if (to.name === "login") {
    if (ready && userReady) return { name: "monitor" };
    return true;
  }
  if (!ready || !userReady) return { name: "login" };
  return true;
});

export default router;

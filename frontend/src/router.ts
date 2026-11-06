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

// 已经进过工作台之后，只改 ?settings= 这类查询时不要再整页送回登录。
let admitted = false;

router.beforeEach(async (to, from) => {
  let status = null;
  try {
    status = await fetchAuthStatus();
  } catch {
    status = null;
  }
  const ready = consoleReady(status);
  const staying =
    admitted && ready && from.name === "monitor" && to.name === "monitor";
  if (staying) {
    authRequired.value = false;
    return true;
  }
  const userState = ready ? await hasTelegramUser() : false;
  // 列表请求失败时沿用上一次的结论，避免一次失败就把人送回登录页。
  const userReady = userState === null ? admitted : userState;
  admitted = ready && userReady;
  authRequired.value = !admitted;
  if (to.name === "login") {
    if (admitted) return { name: "monitor" };
    return true;
  }
  if (!admitted) return { name: "login" };
  return true;
});

export default router;

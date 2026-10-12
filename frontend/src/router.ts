/**
 * @file router.ts
 * @description 前端单页应用 (SPA) 路由配置中心
 *
 * `/` 是工作台。`/settings` 会回到 `/?settings=`，由工作台打开设置层。
 */

import { createRouter, createWebHistory } from "vue-router";
import MonitorPage from "./pages/MonitorPage.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
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

export default router;

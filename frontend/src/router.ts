/**
 * @file router.ts
 * @description 前端单页应用 (SPA) 路由配置中心
 *
 * 采用 HTML5 History 模式（createWebHistory），实现干净无哈希的现代 Web URL：
 * - `/`: 监控控制台主页（Worker 阵列、实时传输流、全局遥测大盘）
 * - `/settings`: 系统配置页（API Token 鉴权管理、upload.toml 参数热更新）
 *
 * 注：后端 app.py 已配合配置 `/{path:path}` SPA fallback 路由，
 * 保证任意路由直接刷新不会出现 404。
 */

import { createRouter, createWebHistory } from "vue-router";
import MonitorPage from "./pages/MonitorPage.vue";
import SettingsPage from "./pages/SettingsPage.vue";

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
      name: "settings",
      component: SettingsPage,
      meta: { title: "系统配置" },
    },
  ],
});

export default router;

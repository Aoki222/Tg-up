/**
 * @file main.ts
 * @description 前端应用初始化与启动入口
 *
 * 职责：
 * 1. 实例化 Vue 根应用，并挂载至 index.html 的 `#app` 节点；
 * 2. 注入 Element Plus。界面语言由 ElConfigProvider 按浏览器选择提供，默认英文；
 * 3. 注册 Vue Router (HTML5 History 路由)；
 * 4. 注册 Vue Query (TanStack Query)，配置全局数据缓存保鲜（staleTime）；
 * 5. 导入 Apple / Craft 润白精密设计系统的全局样式 (style.css)。
 */

import { createApp } from "vue";
import ElementPlus from "element-plus";
import "element-plus/dist/index.css";
import { VueQueryPlugin, QueryClient } from "@tanstack/vue-query";
import App from "./App.vue";
import router from "./router";
import "./style.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10 * 1000, // 10秒内数据视为新鲜，切标签页不触发重复网络请求
      gcTime: 5 * 60 * 1000, // 5分钟无引用时清理缓存
      refetchOnWindowFocus: false, // 窗口聚焦不盲目重刷
      retry: 1,
    },
  },
});

const app = createApp(App);

app.use(VueQueryPlugin, { queryClient });

// 语言包由 App 里的 ElConfigProvider 按当前选择提供，这里不再锁死 zh-cn。
app.use(ElementPlus);

// 注册路由中枢
app.use(router);

// 挂载 DOM
app.mount("#app");

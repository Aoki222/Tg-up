/**
 * @file main.ts
 * @description 前端应用初始化与启动入口
 *
 * 职责：
 * 1. 实例化 Vue 根应用，并挂载至 index.html 的 `#app` 节点；
 * 2. 注入 Element Plus。界面语言由 ElConfigProvider 按浏览器选择提供，默认英文；
 * 3. 注册 Vue Router (HTML5 History 路由)；
 * 4. 导入 Apple / Craft 润白精密设计系统的全局样式 (style.css)。
 */

import { createApp } from "vue";
import ElementPlus from "element-plus";
import "element-plus/dist/index.css";
import App from "./App.vue";
import router from "./router";
import "./style.css";

const app = createApp(App);

// 语言包由 App 里的 ElConfigProvider 按当前选择提供，这里不再锁死 zh-cn。
app.use(ElementPlus);

// 注册路由中枢
app.use(router);

// 挂载 DOM
app.mount("#app");

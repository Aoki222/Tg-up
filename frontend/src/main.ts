/**
 * @file main.ts
 * @description 前端应用初始化与启动入口
 *
 * 职责：
 * 1. 实例化 Vue 根应用，并挂载至 index.html 的 `#app` 节点；
 * 2. 注入 Element Plus UI 框架及全量中文语言包 (zh-cn)；
 * 3. 注册 Vue Router (HTML5 History 路由)；
 * 4. 导入 Apple / Craft 润白精密设计系统的全局样式 (style.css)。
 */

import { createApp } from "vue";
import ElementPlus from "element-plus";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import "element-plus/dist/index.css";
import App from "./App.vue";
import router from "./router";
import "./style.css";

const app = createApp(App);

// 配置 Element Plus 全局中文国际化
app.use(ElementPlus, { locale: zhCn });

// 注册路由中枢
app.use(router);

// 挂载 DOM
app.mount("#app");

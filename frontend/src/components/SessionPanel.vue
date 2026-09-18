<script setup lang="ts">
/**
 * @file SessionPanel.vue
 * @description Telegram 授权登录与 Session 创建弹窗面板
 *
 * 核心业务流程（多步骤状态机）：
 * 1. 【初始表单阶段 (step='form')】：
 *    - 用户选择 Bot Token 凭证登录 或 手机号登录；
 *    - 可选是否在登录成功后立即向目标 Telegram 群组发送鉴权探针以验证群管理发帖权限；
 * 2. 【短信/Telegram验证码阶段 (step='code')】：
 *    - 用户手机登录时，输入服务端通过 MTProto 下发的 Telegram 官方服务通知验证码；
 * 3. 【两步验证密码阶段 (step='password')】：
 *    - 若 Telegram 账号启用了 2FA 密码保护，服务端返回要求提交两步验证云密码；
 * 4. 【完结持久化 (step='done')】：
 *    - 服务端成功在 sessions/ 目录下生成 <username>.session 凭证；
 *    - 约 2 秒后后端 SessionPool 自动探测并拉起为常驻工作 Worker。
 */

import { onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import {
  fetchSessionMeta,
  startSessionLogin,
  submitSessionCode,
  submitSessionPassword,
} from "../api";
import type { SessionLoginResult, SessionMode } from "../types";

// ── 事件声明 ───────────────────────────────────────────────────

const emit = defineEmits<{
  /** 通知父组件关闭当前模态弹窗 */
  close: [];
}>();

// ── 响应式状态 ─────────────────────────────────────────────────

/** 异步请求 loading 状态 */
const loading = ref(false);

/** 本地已有 session 文件名列表 */
const existing = ref<string[]>([]);

/** 当前状态机所处的步骤 */
const step = ref<"form" | "code" | "password">("form");

/** 服务端返回的登录事务标识 login_id */
const loginId = ref("");

/** 验证码输入值 */
const code = ref("");

/** 2FA 两步验证密码输入值 */
const password = ref("");

/** 初始提交表单模型 */
const form = reactive({
  mode: "bot" as SessionMode,
  bot_token: "",
  phone: "",
  bind_group: false,
  group_id: undefined as number | undefined,
  force: false,
});

// ── 登录业务流程处理 ───────────────────────────────────────────

/** 加载已有 Session 元数据与默认群组 chat_id */
async function loadMeta(): Promise<void> {
  try {
    const meta = await fetchSessionMeta();
    existing.value = meta.items;
    if (form.group_id == null && meta.default_group_id) {
      form.group_id = meta.default_group_id;
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "无法读取 session 列表");
  }
}

/**
 * 推进登录步骤状态机
 */
function handleResult(result: SessionLoginResult): void {
  // 群组验证告警（如果要求绑定群组但发帖探针失败）
  if (result.group_ok === false) {
    ElMessage.warning(`Session 已保存，但群组验证失败：${result.group_error || ""}`);
  }

  // 流程完结：登录成功
  if (result.done) {
    ElMessage.success(result.message || "已创建 session");
    void loadMeta();
    close();
    return;
  }

  // 步进到验证码输入
  if (result.step === "code") {
    loginId.value = result.login_id || "";
    step.value = "code";
    ElMessage.info(result.message || "请输入验证码");
    return;
  }

  // 步进到两步验证密码输入
  if (result.step === "password") {
    loginId.value = result.login_id || "";
    step.value = "password";
    ElMessage.info(result.message || "请输入两步验证密码");
  }
}

/** 发起初始创建握手 */
async function start(): Promise<void> {
  loading.value = true;
  try {
    const result = await startSessionLogin({
      mode: form.mode,
      bot_token: form.bot_token,
      phone: form.phone,
      bind_group: form.bind_group,
      group_id: form.bind_group ? form.group_id ?? null : null,
      force: form.force,
    });
    handleResult(result);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "创建失败");
  } finally {
    loading.value = false;
  }
}

/** 提交手机验证码 */
async function sendCode(): Promise<void> {
  loading.value = true;
  try {
    handleResult(await submitSessionCode(loginId.value, code.value));
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "验证码失败");
  } finally {
    loading.value = false;
  }
}

/** 提交两步验证云密码 */
async function sendPassword(): Promise<void> {
  loading.value = true;
  try {
    handleResult(await submitSessionPassword(loginId.value, password.value));
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "密码失败");
  } finally {
    loading.value = false;
  }
}

/** 重置状态并关闭弹窗 */
function close(): void {
  step.value = "form";
  loginId.value = "";
  code.value = "";
  password.value = "";
  emit("close");
}

onMounted(() => {
  void loadMeta();
});
</script>

<template>
  <el-card shadow="never" class="session-card">
    <template #header>
      <div class="head">
        <span>添加 Session 凭证</span>
        <el-button size="small" @click="close">关闭</el-button>
      </div>
    </template>

    <el-alert
      title="登录过程由服务端直接与 Telegram MTProto 交互完成。生成后约 2 秒会被系统自动探测拉起为活跃 Worker，无需重启进程。"
      type="info"
      show-icon
      :closable="false"
      class="banner"
    />

    <!-- 已有 Session 标签一览 -->
    <div class="existing" v-if="existing.length">
      已有凭证：
      <el-tag v-for="name in existing" :key="name" class="tag" effect="plain">{{ name }}</el-tag>
    </div>

    <!-- 步骤 1：初始输入表单 -->
    <el-form v-if="step === 'form'" label-position="top" class="form">
      <el-form-item label="登录模式">
        <el-radio-group v-model="form.mode">
          <el-radio-button label="bot">Bot Token 机器人</el-radio-button>
          <el-radio-button label="user">手机号用户账号</el-radio-button>
        </el-radio-group>
      </el-form-item>

      <el-form-item v-if="form.mode === 'bot'" label="Bot Token">
        <el-input v-model="form.bot_token" placeholder="形如 123456:AAH..." show-password />
      </el-form-item>
      <el-form-item v-else label="手机号">
        <el-input v-model="form.phone" placeholder="含国际区号，如 +86138..." />
      </el-form-item>

      <el-form-item>
        <el-switch v-model="form.bind_group" active-text="立即向目标群组发送探针以验证权限" />
      </el-form-item>
      <el-form-item v-if="form.bind_group" label="群组 chat_id">
        <el-input-number v-model="form.group_id" :controls="false" class="grow" />
      </el-form-item>
      <el-form-item>
        <el-checkbox v-model="form.force">允许覆盖已存在的同名 Session</el-checkbox>
      </el-form-item>

      <el-button type="primary" :loading="loading" @click="start">开始创建登录</el-button>
    </el-form>

    <!-- 步骤 2：提交手机验证码 -->
    <el-form v-else-if="step === 'code'" label-position="top">
      <el-form-item label="短信 / Telegram 官方服务通知验证码">
        <el-input v-model="code" maxlength="8" placeholder="输入 5~8 位验证码" />
      </el-form-item>
      <el-button type="primary" :loading="loading" @click="sendCode">提交验证码</el-button>
      <el-button @click="close">取消</el-button>
    </el-form>

    <!-- 步骤 3：提交两步验证密码 -->
    <el-form v-else label-position="top">
      <el-form-item label="账号两步验证密码 (2FA Cloud Password)">
        <el-input v-model="password" show-password placeholder="输入两步验证密码" />
      </el-form-item>
      <el-button type="primary" :loading="loading" @click="sendPassword">提交密码</el-button>
      <el-button @click="close">取消</el-button>
    </el-form>
  </el-card>
</template>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
}

.banner {
  margin-bottom: 14px;
}

.existing {
  margin-bottom: 14px;
  color: var(--text-secondary);
  font-size: 13px;
}

.tag {
  margin: 0 6px 6px 0;
}

.form :deep(.el-input-number) {
  width: 100%;
}

.grow {
  width: 100%;
}
</style>

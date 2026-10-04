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
 * 4. 【二维码 (step='qr')】：
 *    - 服务端只返回 tg://login 链接，本组件用 qrcode 画成图；
 *    - 每 1.5 秒轮询，链接变了就重画。扫码后若开了两步验证，进入 password。
 * 5. 【完结持久化 (step='done')】：
 *    - 服务端成功在 sessions/ 目录下生成 <username>.session 凭证；
 *    - 约 2 秒后后端 SessionPool 自动探测并拉起为常驻工作 Worker。
 */

import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import QRCode from "qrcode";
import {
  cancelSessionLogin,
  pollSessionLogin,
  startSessionLogin,
  submitSessionCode,
  submitSessionPassword,
} from "../api";
import { refreshSessionAccounts, reloadSessionAccounts, useSessionAccounts } from "../composables/useSessionAccounts";
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
const { accounts, names: existing } = useSessionAccounts();
const userAccounts = computed(() => accounts.value.filter((item) => item.kind === "user"));
const botAccounts = computed(() => accounts.value.filter((item) => item.kind === "bot"));
const unknownAccounts = computed(() => accounts.value.filter((item) => item.kind === "unknown"));

/** 当前状态机所处的步骤 */
const step = ref<"form" | "code" | "password" | "qr">("form");
const apiConfigured = ref<boolean | null>(null);
const qrUrl = ref("");
const qrImage = ref("");
const qrSecondsLeft = ref(0);
let qrTimer = 0;
let qrClock = 0;

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
    const meta = await refreshSessionAccounts();
    apiConfigured.value = meta.api_configured;
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
    void reloadSessionAccounts();
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
    stopQrPoll();
    stopQrClock();
    loginId.value = result.login_id || "";
    step.value = "password";
    ElMessage.info(result.message || "请输入两步验证密码");
    return;
  }

  if (result.step === "qr") {
    loginId.value = result.login_id || "";
    step.value = "qr";
    void showQr(result);
    startQrPoll();
  }
}

function stopQrClock(): void {
  window.clearInterval(qrClock);
  qrClock = 0;
}

function armQrCountdown(seconds: number): void {
  stopQrClock();
  const total = Math.max(0, Math.floor(seconds));
  qrSecondsLeft.value = total;
  if (total <= 0) return;
  const started = Date.now();
  qrClock = window.setInterval(() => {
    const left = total - Math.floor((Date.now() - started) / 1000);
    qrSecondsLeft.value = Math.max(0, left);
    if (left <= 0) stopQrClock();
  }, 250);
}

async function showQr(result: SessionLoginResult): Promise<void> {
  // 后端可能在二维码过期后返回新 URL；只有 URL 变化时才重新绘制。
  const url = result.qr_url || "";
  if (!url || url === qrUrl.value) return;
  qrUrl.value = url;
  armQrCountdown(result.qr_expires_in ?? 0);
  qrImage.value = await QRCode.toDataURL(url, {
    width: 260,
    margin: 2,
    errorCorrectionLevel: "M",
  });
}

function stopQrPoll(): void {
  // 进入密码/完成/关闭状态时停止轮询，避免继续访问已结束的 login_id。
  window.clearTimeout(qrTimer);
  qrTimer = 0;
}

function startQrPoll(): void {
  // 前端不维持 WebSocket；用递归 setTimeout 避免请求堆积。
  stopQrPoll();
  async function tick() {
    if (!loginId.value) return;
    try {
      const result = await pollSessionLogin(loginId.value);
      if (result.step === "qr") {
        await showQr(result);
      } else {
        handleResult(result);
        return;
      }
    } catch (error) {
      ElMessage.error(error instanceof Error ? error.message : "二维码登录失败");
      return;
    }
    qrTimer = window.setTimeout(tick, 1500);
  }
  qrTimer = window.setTimeout(tick, 1500);
}

/** 发起初始创建握手 */
async function start(): Promise<void> {
  if (apiConfigured.value === false) {
    ElMessage.warning("尚未配置 Telegram API 凭据，无法添加账号");
    return;
  }
  // 发起登录请求。QR 模式下请求会等待现场 connect() 和 qr_login() 完成，期间显示 loading。
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
  // 把验证码交给后端原客户端，结果可能推进到 password 或 done。
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
  // 提交 2FA 后由 handleResult 统一处理成功或错误状态。
  loading.value = true;
  try {
    handleResult(await submitSessionPassword(loginId.value, password.value));
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "密码失败");
  } finally {
    loading.value = false;
  }
}

/** 取消当前手机号登录，回到表单修改号码。 */
async function backToPhone(): Promise<void> {
  stopQrPoll();
  stopQrClock();
  const activeLoginId = loginId.value;
  loginId.value = "";
  code.value = "";
  password.value = "";
  qrUrl.value = "";
  qrImage.value = "";
  qrSecondsLeft.value = 0;
  form.mode = "user";
  step.value = "form";
  await cancelActiveLogin(activeLoginId);
}

/** 重置状态并关闭弹窗 */
async function close(): Promise<void> {
  // 重置前端状态；当前登录连接的释放由后端 pending 生命周期负责。
  stopQrPoll();
  stopQrClock();
  const activeLoginId = loginId.value;
  await cancelActiveLogin(activeLoginId);
  step.value = "form";
  loginId.value = "";
  code.value = "";
  password.value = "";
  qrUrl.value = "";
  qrImage.value = "";
  qrSecondsLeft.value = 0;
  emit("close");
}

/**
 * 取消当前登录事务。按钮关闭和组件卸载都会走这里，覆盖遮罩点击、路由切换等退出方式。
 */
async function cancelActiveLogin(activeLoginId: string): Promise<void> {
  if (!activeLoginId) return;
  try {
    await cancelSessionLogin(activeLoginId);
  } catch {
    // 后端有五分钟过期清理作为兜底，不能阻塞前端卸载。
  }
}

watch(
  () => form.mode,
  (mode) => {
    if (mode === "qr" && step.value === "form" && !loading.value) {
      if (apiConfigured.value === false) {
        ElMessage.warning("尚未配置 Telegram API 凭据，无法添加账号");
        form.mode = "bot";
        return;
      }
      step.value = "qr";
      qrImage.value = "";
      void start();
    }
  },
);

onMounted(() => {
  void loadMeta();
});
onUnmounted(() => {
  stopQrPoll();
  stopQrClock();
  // 遮罩点击或父组件 v-if 卸载不会调用 close()，这里必须主动取消后端 pending。
  void cancelActiveLogin(loginId.value);
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
      v-if="apiConfigured === false"
      type="warning"
      show-icon
      :closable="false"
      class="banner"
    >
      <template #title>尚未配置 Telegram API 凭据，无法添加账号</template>
      <router-link to="/settings/account">前往账号设置</router-link>
    </el-alert>
    <el-alert
      v-else
      title="登录过程由服务端直接与 Telegram MTProto 交互完成。生成后约 2 秒会被系统自动探测拉起为活跃 Worker，无需重启进程。"
      type="info"
      show-icon
      :closable="false"
      class="banner"
    />

    <!-- 已有 Session 标签一览 -->
    <div class="account-lists" v-if="accounts.length || existing.length">
      <div>
        <div class="list-label">用户号</div>
        <div class="existing">
          <el-tag v-for="item in userAccounts" :key="item.name" class="tag" effect="plain">
            {{ item.username ? `@${item.username}` : item.name }}
          </el-tag>
          <span v-if="!userAccounts.length" class="muted">还没有</span>
        </div>
      </div>
      <div>
        <div class="list-label">Bot</div>
        <div class="existing">
          <el-tag v-for="item in botAccounts" :key="item.name" class="tag" type="info" effect="plain">
            {{ item.username ? `@${item.username}` : item.name }}
          </el-tag>
          <span v-if="!botAccounts.length" class="muted">还没有</span>
        </div>
      </div>
      <div v-if="unknownAccounts.length">
        <div class="list-label">未标记</div>
        <div class="existing">
          <el-tag v-for="item in unknownAccounts" :key="item.name" class="tag" effect="plain">{{ item.name }}</el-tag>
        </div>
      </div>
    </div>

    <!-- 步骤 1：初始输入表单 -->
    <el-form v-if="step === 'form'" label-position="top" class="form">
      <el-form-item label="登录模式">
        <el-radio-group v-model="form.mode">
          <el-radio-button value="bot">Bot Token</el-radio-button>
          <el-radio-button value="user">手机号</el-radio-button>
          <el-radio-button value="qr">二维码</el-radio-button>
        </el-radio-group>
      </el-form-item>

      <el-form-item v-if="form.mode === 'bot'" label="Bot Token">
        <el-input v-model="form.bot_token" placeholder="形如 123456:AAH..." show-password />
      </el-form-item>
      <el-form-item v-else-if="form.mode === 'user'" label="手机号">
        <el-input v-model="form.phone" placeholder="含国际区号，如 +86138..." />
      </el-form-item>
      <p v-else class="qr-hint">正在向 Telegram 申请二维码…</p>

      <el-form-item>
        <el-switch v-model="form.bind_group" active-text="立即向目标群组发送探针以验证权限" />
      </el-form-item>
      <el-form-item v-if="form.bind_group" label="群组 chat_id">
        <el-input-number v-model="form.group_id" :controls="false" class="grow" />
      </el-form-item>
      <el-form-item>
        <el-checkbox v-model="form.force">允许覆盖已存在的同名 Session</el-checkbox>
      </el-form-item>

      <el-button type="primary" :loading="loading" :disabled="apiConfigured === false" @click="start">开始创建登录</el-button>
    </el-form>

    <div v-else-if="step === 'qr'" class="qr-box">
      <div v-if="loading" class="qr-loading" role="status" aria-live="polite">
        <span class="spinner" aria-hidden="true"></span>
        <p class="qr-hint">正在连接 Telegram，二维码即将生成…</p>
      </div>
      <template v-else>
        <img v-if="qrImage" class="qr-image" :src="qrImage" alt="Telegram 登录二维码" />
        <p v-else class="qr-hint">正在生成二维码…</p>
      </template>
      <p class="qr-hint">打开 Telegram，进入 设置 → 设备 → 关联桌面设备，扫描这个二维码。</p>
      <p v-if="qrSecondsLeft > 0" class="qr-hint">这张二维码将在 {{ qrSecondsLeft }} 秒后更换。</p>
      <p v-else class="qr-hint">过期后会自动换一张。</p>
      <el-button @click="close">取消</el-button>
    </div>

    <!-- 步骤 2：提交手机验证码 -->
    <el-form v-else-if="step === 'code'" label-position="top">
      <el-alert
        title="🔔 Telegram 验证码已发送至您其他已登录客户端的服务通知（非手机短信），请打开 Telegram 查收。"
        type="info"
        show-icon
        :closable="false"
        class="banner"
      />
      <el-form-item label="Telegram 验证码">
        <el-input v-model="code" maxlength="8" placeholder="输入 5~8 位验证码" />
      </el-form-item>
      <el-button type="primary" :loading="loading" @click="sendCode">提交验证码</el-button>
      <el-button @click="backToPhone">返回修改手机号</el-button>
      <el-button @click="close">取消</el-button>
    </el-form>

    <!-- 步骤 3：提交两步验证密码 -->
    <el-form v-else label-position="top">
      <el-form-item label="账号两步验证密码 (2FA Cloud Password)">
        <el-input v-model="password" show-password placeholder="输入两步验证密码" />
      </el-form-item>
      <el-button type="primary" :loading="loading" @click="sendPassword">提交密码</el-button>
      <el-button @click="backToPhone">返回修改手机号</el-button>
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

.account-lists {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 14px;
}

.list-label {
  margin-bottom: 4px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text);
}

.existing {
  color: var(--text-secondary);
  font-size: 13px;
}

.muted {
  font-size: 12px;
  color: var(--text-secondary);
}

.qr-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}

.qr-loading {
  display: flex;
  min-height: 260px;
  width: 260px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface-muted, #f7f8fa);
}

.qr-loading .qr-hint {
  margin: 0;
  text-align: center;
}

.spinner {
  width: 30px;
  height: 30px;
  border: 3px solid var(--border);
  border-top-color: var(--primary, #409eff);
  border-radius: 50%;
  animation: qr-spin 0.8s linear infinite;
}

@keyframes qr-spin {
  to {
    transform: rotate(360deg);
  }
}

.qr-image {
  width: 260px;
  height: 260px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: #fff;
}

.qr-hint {
  max-width: 320px;
  margin: 0 0 8px;
  font-size: 12px;
  line-height: 1.5;
  text-align: center;
  color: var(--text-secondary);
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

@media (max-width: 768px) {
  .qr-image,
  .qr-loading {
    width: 220px;
    height: 220px;
    min-height: 220px;
  }

  .qr-hint {
    max-width: 260px;
  }
}
</style>

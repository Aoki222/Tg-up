<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import {
  fetchSessionMeta,
  startSessionLogin,
  submitSessionCode,
  submitSessionPassword,
} from "../api";
import type { SessionLoginResult, SessionMode } from "../types";

const emit = defineEmits<{ close: [] }>();
const loading = ref(false);
const existing = ref<string[]>([]);
const step = ref<"form" | "code" | "password">("form");
const loginId = ref("");
const code = ref("");
const password = ref("");

const form = reactive({
  mode: "bot" as SessionMode,
  bot_token: "",
  phone: "",
  bind_group: false,
  group_id: undefined as number | undefined,
  force: false,
});

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

function handleResult(result: SessionLoginResult): void {
  if (result.group_ok === false) {
    ElMessage.warning(`Session 已保存，但群组验证失败：${result.group_error || ""}`);
  }
  if (result.done) {
    ElMessage.success(result.message || "已创建 session");
    void loadMeta();
    close();
    return;
  }
  if (result.step === "code") {
    loginId.value = result.login_id || "";
    step.value = "code";
    ElMessage.info(result.message || "请输入验证码");
    return;
  }
  if (result.step === "password") {
    loginId.value = result.login_id || "";
    step.value = "password";
    ElMessage.info(result.message || "请输入两步验证密码");
  }
}

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
  <el-card shadow="never">
    <template #header>
      <div class="head">
        <span>添加 Session</span>
        <el-button size="small" @click="close">关闭</el-button>
      </div>
    </template>

    <el-alert
      title="登录在服务器上完成，浏览器拿不到 api_hash。生成后约 2 秒会自动变成 Worker，不必重启。"
      type="info"
      show-icon
      :closable="false"
      class="banner"
    />

    <div class="existing" v-if="existing.length">
      已有：
      <el-tag v-for="name in existing" :key="name" class="tag" effect="plain">{{ name }}</el-tag>
    </div>

    <el-form v-if="step === 'form'" label-position="top" class="form">
      <el-form-item label="类型">
        <el-radio-group v-model="form.mode">
          <el-radio-button label="bot">Bot Token</el-radio-button>
          <el-radio-button label="user">手机号用户</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item v-if="form.mode === 'bot'" label="Bot Token">
        <el-input v-model="form.bot_token" placeholder="123456:AAH..." show-password />
      </el-form-item>
      <el-form-item v-else label="手机号">
        <el-input v-model="form.phone" placeholder="+86138..." />
      </el-form-item>
      <el-form-item>
        <el-switch v-model="form.bind_group" active-text="绑定并验证指定群组" />
      </el-form-item>
      <el-form-item v-if="form.bind_group" label="群组 chat_id">
        <el-input-number v-model="form.group_id" :controls="false" class="grow" />
      </el-form-item>
      <el-form-item>
        <el-checkbox v-model="form.force">覆盖已有同名 session</el-checkbox>
      </el-form-item>
      <el-button type="primary" :loading="loading" @click="start">开始创建</el-button>
    </el-form>

    <el-form v-else-if="step === 'code'" label-position="top">
      <el-form-item label="短信 / Telegram 验证码">
        <el-input v-model="code" maxlength="8" />
      </el-form-item>
      <el-button type="primary" :loading="loading" @click="sendCode">提交验证码</el-button>
      <el-button @click="close">取消</el-button>
    </el-form>

    <el-form v-else label-position="top">
      <el-form-item label="两步验证密码">
        <el-input v-model="password" show-password />
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
  align-items: baseline;
  flex-wrap: wrap;
}
.hint {
  color: var(--muted);
  font-size: 12px;
  font-weight: 400;
}
.banner {
  margin-bottom: 14px;
}
.existing {
  margin-bottom: 14px;
  color: var(--muted);
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

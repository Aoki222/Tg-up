<script setup lang="ts">
/**
 * 控制台登录卡。初始账号登录后，在改掉密码之前不能进入工作台。
 */
import { computed, reactive, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { changeAdminPassword, loginAdmin, type AuthStatus } from "../api";
import { t } from "../i18n";

const props = defineProps<{
  status: AuthStatus;
}>();

const emit = defineEmits<{
  done: [];
}>();

const loading = ref(false);
const form = reactive({
  username: "",
  password: "",
  currentPassword: "",
  newPassword: "",
  newPasswordAgain: "",
});

const mustChange = computed(() => props.status.authenticated && props.status.must_change_password);

watch(
  () => props.status.username,
  (name) => {
    if (name && !form.username) form.username = name;
  },
  { immediate: true },
);

async function submitLogin(): Promise<void> {
  loading.value = true;
  try {
    await loginAdmin({ username: form.username, password: form.password });
    emit("done");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t("auth.failed"));
  } finally {
    loading.value = false;
  }
}

async function submitChange(): Promise<void> {
  if (form.newPassword !== form.newPasswordAgain) {
    ElMessage.error(t("auth.passwordMismatch"));
    return;
  }
  loading.value = true;
  try {
    await changeAdminPassword({
      current_password: form.currentPassword || form.password,
      new_password: form.newPassword,
      new_password_again: form.newPasswordAgain,
      username: form.username.trim(),
    });
    emit("done");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t("auth.failed"));
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <el-card shadow="never" class="session-card auth-card">
    <template #header>
      <div class="head">
        <span>{{ mustChange ? t("auth.mustChangeTitle") : t("auth.loginTitle") }}</span>
      </div>
    </template>

    <p class="hint">{{ mustChange ? t("auth.mustChangeHint") : t("auth.loginHint") }}</p>

    <el-form v-if="!mustChange" label-position="top" class="form" @submit.prevent="submitLogin">
      <el-form-item :label="t('auth.username')">
        <el-input v-model="form.username" autocomplete="username" />
      </el-form-item>
      <el-form-item :label="t('auth.password')">
        <el-input v-model="form.password" type="password" show-password autocomplete="current-password" />
      </el-form-item>
      <el-button type="primary" :loading="loading" native-type="submit">{{ t("auth.login") }}</el-button>
    </el-form>

    <el-form v-else label-position="top" class="form" @submit.prevent="submitChange">
      <el-form-item :label="t('auth.username')">
        <el-input v-model="form.username" autocomplete="username" maxlength="32" />
      </el-form-item>
      <el-form-item :label="t('auth.currentPassword')">
        <el-input v-model="form.currentPassword" type="password" show-password autocomplete="current-password" />
      </el-form-item>
      <el-form-item :label="t('auth.passwordNew')">
        <el-input v-model="form.newPassword" type="password" show-password autocomplete="new-password" />
      </el-form-item>
      <el-form-item :label="t('auth.passwordAgain')">
        <el-input v-model="form.newPasswordAgain" type="password" show-password autocomplete="new-password" />
      </el-form-item>
      <el-button type="primary" :loading="loading" native-type="submit">{{ t("auth.changePassword") }}</el-button>
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

.hint {
  margin: 0 0 14px;
  font-size: 13px;
  color: var(--text-secondary);
}

.form {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
</style>

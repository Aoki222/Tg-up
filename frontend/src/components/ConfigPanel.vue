<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import { fetchSettings, saveSettings } from "../api";
import type { UploadConfig } from "../types";

const loading = ref(false);
const saving = ref(false);
const savedSnapshot = ref("");
const form = reactive<UploadConfig>({
  chat_id: 0,
  observer_paths: ["download"],
  observer_path_infos: [],
  page_dir: "page",
  archive_dir: "uploaded",
  preview: "first_frame",
  topic_creation_enabled: true,
  after_success: "keep",
  concurrency: 3,
  max_retries: 3,
  upload_timeout_seconds: 1200,
  assigned_timeout_seconds: 600,
  stable_timeout_seconds: 1800,
  watch_extensions: ["mp4", "mkv", "avi", "mov", "wmv", "m4v"],
});

function snapshotOf(config: UploadConfig): string {
  return JSON.stringify({
    ...config,
    observer_paths: [...config.observer_paths].map((item) => item.trim()).sort(),
    watch_extensions: [...config.watch_extensions].map((item) => item.trim()).sort(),
  });
}

function applyServer(data: UploadConfig): void {
  Object.assign(form, data);
  savedSnapshot.value = snapshotOf({ ...form });
}

const dirty = computed(() => savedSnapshot.value !== "" && snapshotOf({ ...form }) !== savedSnapshot.value);

async function load(): Promise<void> {
  loading.value = true;
  try {
    applyServer(await fetchSettings());
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "读取配置失败");
  } finally {
    loading.value = false;
  }
}

async function submit(): Promise<void> {
  if (!dirty.value) {
    return;
  }
  saving.value = true;
  try {
    applyServer(await saveSettings({ ...form }));
    ElMessage.success("已写入 upload.toml，配置立即生效");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "保存失败");
  } finally {
    saving.value = false;
  }
}

onMounted(() => {
  void load();
});
</script>

<template>
  <el-card v-loading="loading" shadow="never">
    <template #header>
      <div class="head">
        <span>上传配置</span>
        <el-button type="primary" :loading="saving" :disabled="!dirty" @click="submit">
          保存到 upload.toml
        </el-button>
      </div>
    </template>
    <el-alert
      title="这里改的是 upload.toml，保存后热加载。监听目录可填相对或绝对路径，相对路径相对进程工作目录解析；不存在的目录不会自动创建，也不会开始监听。"
      type="info"
      show-icon
      :closable="false"
      class="hint"
    />
    <el-form label-position="top" class="form">
      <div class="cols">
        <el-form-item label="目标群 chat_id">
          <el-input-number v-model="form.chat_id" :controls="false" class="grow" />
        </el-form-item>
        <el-form-item label="封面">
          <el-select v-model="form.preview" class="grow">
            <el-option label="关闭" value="off" />
            <el-option label="首帧" value="first_frame" />
            <el-option label="网格" value="grid" />
          </el-select>
        </el-form-item>
        <el-form-item label="上传成功后">
          <el-select v-model="form.after_success" class="grow">
            <el-option label="保留本地文件" value="keep" />
            <el-option label="删除本地文件" value="delete" />
            <el-option label="归档到 uploaded/" value="move_to_archive" />
          </el-select>
        </el-form-item>
        <el-form-item label="创建论坛话题">
          <el-switch v-model="form.topic_creation_enabled" />
        </el-form-item>
      </div>
      <el-form-item label="监听目录（可多条）">
        <el-select
          v-model="form.observer_paths"
          multiple
          filterable
          allow-create
          default-first-option
          placeholder="相对或绝对路径，回车添加"
          class="grow"
        />
        <ul v-if="form.observer_path_infos.length" class="path-hints">
          <li v-for="item in form.observer_path_infos" :key="item.path">
            <span>{{ item.path }}</span>
            <span v-if="item.ok" class="ok">可用</span>
            <span v-else class="bad">{{ item.error || "目录不存在" }}</span>
          </li>
        </ul>
      </el-form-item>
      <div class="cols">
        <el-form-item label="封面目录">
          <el-input v-model="form.page_dir" />
        </el-form-item>
        <el-form-item label="归档目录">
          <el-input v-model="form.archive_dir" />
        </el-form-item>
        <el-form-item label="每账号并发">
          <el-input-number v-model="form.concurrency" :min="1" :max="32" />
        </el-form-item>
      </div>
      <div class="cols">
        <el-form-item label="最大重试">
          <el-input-number v-model="form.max_retries" :min="1" :max="20" />
        </el-form-item>
        <el-form-item label="上传超时（秒）">
          <el-input-number v-model="form.upload_timeout_seconds" :min="30" :step="30" />
        </el-form-item>
        <el-form-item label="抢占超时（秒）">
          <el-input-number v-model="form.assigned_timeout_seconds" :min="30" :step="30" />
        </el-form-item>
        <el-form-item label="写稳等待（秒）">
          <el-input-number v-model="form.stable_timeout_seconds" :min="1" :step="30" />
        </el-form-item>
      </div>
      <el-form-item label="监听格式">
        <el-select
          v-model="form.watch_extensions"
          multiple
          filterable
          allow-create
          default-first-option
          placeholder="留空=任意格式；回车添加 zip / pdf / mp4 …"
          class="grow"
        />
      </el-form-item>
    </el-form>
  </el-card>
</template>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
.hint {
  margin-bottom: 18px;
}
.form :deep(.el-input-number) {
  width: 100%;
}
.cols {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0 16px;
}
.grow {
  width: 100%;
}
.path-hints {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  font-size: 12px;
  color: var(--muted);
}
.path-hints li {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 0;
  word-break: break-all;
}
.path-hints .ok {
  color: var(--ok);
  flex-shrink: 0;
}
.path-hints .bad {
  color: var(--bad);
  flex-shrink: 0;
}
@media (max-width: 900px) {
  .cols {
    grid-template-columns: 1fr 1fr;
  }
}
@media (max-width: 560px) {
  .cols {
    grid-template-columns: 1fr;
  }
}
</style>

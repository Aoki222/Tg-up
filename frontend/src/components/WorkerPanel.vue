<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { deleteWorker, disableWorker, enableWorker, fetchWorkers } from "../api";
import type { WorkerSnapshot } from "../types";

const emit = defineEmits<{ add: [] }>();

const workers = ref<WorkerSnapshot[]>([]);
const error = ref("");
let timer = 0;

async function refresh(): Promise<void> {
  try {
    workers.value = await fetchWorkers();
    error.value = "";
  } catch {
    error.value = "无法读取 worker";
  }
}

onMounted(() => {
  void refresh();
  timer = window.setInterval(() => {
    void refresh();
  }, 1000);
});

onUnmounted(() => {
  window.clearInterval(timer);
});

function statusLabel(worker: WorkerSnapshot): string {
  if (!worker.enabled) {
    return "已禁用";
  }
  if (worker.reconnecting) {
    const wait = worker.reconnect_wait_seconds > 0 ? ` ${worker.reconnect_wait_seconds}s` : "";
    return `重连 ${worker.reconnect_attempt}/${worker.reconnect_max}${wait}`;
  }
  if (worker.flood_wait_seconds > 0) {
    return `限流 ${worker.flood_wait_seconds}s`;
  }
  if (!worker.running || !worker.accepting) {
    return "停接";
  }
  if (worker.in_flight > 0) {
    return "上传中";
  }
  return "空闲";
}

function statusType(worker: WorkerSnapshot): "success" | "primary" | "warning" | "info" | "danger" {
  if (!worker.enabled) {
    return "info";
  }
  if (worker.reconnecting) {
    return "warning";
  }
  if (worker.flood_wait_seconds > 0) {
    return "warning";
  }
  if (!worker.running || !worker.accepting) {
    return "danger";
  }
  if (worker.in_flight > 0) {
    return "primary";
  }
  return "success";
}

const busyName = ref("");

async function onDisable(name: string): Promise<void> {
  busyName.value = name;
  try {
    await disableWorker(name);
    ElMessage.success(`已禁用 ${name}`);
    await refresh();
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "禁用失败");
  } finally {
    busyName.value = "";
  }
}

async function onEnable(name: string): Promise<void> {
  busyName.value = name;
  try {
    await enableWorker(name);
    ElMessage.success(`已启用 ${name}`);
    await refresh();
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "启用失败");
  } finally {
    busyName.value = "";
  }
}

async function onDelete(name: string): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `将删除 sessions/${name}.session，无法恢复，只能重新登录。正在传的任务会释放回队列。`,
      `删除 ${name}`,
      { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  busyName.value = name;
  try {
    await deleteWorker(name);
    ElMessage.success(`已删除 ${name}`);
    await refresh();
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "删除失败");
  } finally {
    busyName.value = "";
  }
}
</script>

<template>
  <el-card shadow="never">
    <template #header>
      <div class="head">
        <span>Workers</span>
        <el-button type="primary" size="small" @click="emit('add')">添加 Session</el-button>
      </div>
    </template>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-empty v-else-if="!workers.length" description="暂无已加载 session" :image-size="72" />
    <div v-else class="list">
      <div v-for="worker in workers" :key="worker.name" class="item">
        <div class="row">
          <div>
            <strong>{{ worker.name }}</strong>
            <div v-if="worker.username" class="user">@{{ worker.username }}</div>
          </div>
          <el-tag :type="statusType(worker)" effect="light" round>
            {{ statusLabel(worker) }}
          </el-tag>
        </div>
        <div class="meta">
          <span>队列 {{ worker.queue_size }}</span>
          <span>在传 {{ worker.in_flight }}</span>
        </div>
        <div v-if="worker.reconnecting && worker.reconnect_error" class="err">
          {{ worker.reconnect_error }}
        </div>
        <div class="actions">
          <el-button
            v-if="worker.enabled"
            size="small"
            :loading="busyName === worker.name"
            @click="onDisable(worker.name)"
          >
            禁用
          </el-button>
          <el-button
            v-else
            size="small"
            type="primary"
            :loading="busyName === worker.name"
            @click="onEnable(worker.name)"
          >
            启用
          </el-button>
          <el-button
            size="small"
            type="danger"
            plain
            :loading="busyName === worker.name"
            @click="onDelete(worker.name)"
          >
            删除
          </el-button>
        </div>
      </div>
    </div>
  </el-card>
</template>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
.list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.item {
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: linear-gradient(180deg, #fff, #f7fbff);
}
.row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}
.user,
.meta {
  color: var(--muted);
  font-size: 12px;
}
.user {
  margin-top: 2px;
}
.meta {
  margin-top: 8px;
  display: flex;
  gap: 14px;
}
.err {
  margin-top: 6px;
  color: var(--bad);
  font-size: 12px;
  word-break: break-all;
}
.actions {
  margin-top: 10px;
  display: flex;
  gap: 8px;
}
</style>

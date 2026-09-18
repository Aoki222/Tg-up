<script setup lang="ts">
/**
 * @file WorkerPanel.vue
 * @description Worker 节点管理面板 (Worker Panel)
 *
 * 核心功能：
 * 1. 【1 秒轻量轮询】：定周期获取所有活跃 Session Worker 的网络与排队状态快照；
 * 2. 【数据向上传递】：获取快照后自动向父组件派发 `updateWorkers` 事件，驱动顶部遥测大盘；
 * 3. 【高阶微徽标 (Micro-Badges)】：
 *    - 上传中（脉冲绿光）、空闲就绪（常亮薄荷绿）、限流/重连（琥珀呼吸光）、停接/异常（红色）、禁用（灰色）；
 * 4. 【生命周期控制】：支持单节点热禁用、热恢复、以及物理销毁 Session 文件（带安全警示确认窗）。
 */

import { onMounted, onUnmounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { deleteWorker, disableWorker, enableWorker, fetchWorkers } from "../api";
import type { WorkerSnapshot } from "../types";

// ── 事件派发 ───────────────────────────────────────────────────

const emit = defineEmits<{
  /** 触发打开添加 Session 模态框 */
  add: [];
  /** 向父组件派发最新的 Worker 快照列表 */
  updateWorkers: [workers: WorkerSnapshot[]];
}>();

// ── 响应式状态 ─────────────────────────────────────────────────

/** 当前加载的 Worker 节点状态快照列表 */
const workers = ref<WorkerSnapshot[]>([]);

/** 轮询异常错误提示 */
const error = ref("");

/** 轮询定时器 ID */
let timer = 0;

/** 当前正在执行异步操作（启用/禁用/删除）的 Worker 名称，用于呈现按钮 loading 状态 */
const busyName = ref("");

// ── 数据拉取与轮询 ─────────────────────────────────────────────

/**
 * 刷新 Worker 节点列表
 */
async function refresh(): Promise<void> {
  try {
    workers.value = await fetchWorkers();
    emit("updateWorkers", workers.value);
    error.value = "";
  } catch {
    error.value = "无法读取 Worker 节点列表";
  }
}

onMounted(() => {
  void refresh();
  // 1 秒心跳轮询状态
  timer = window.setInterval(() => {
    void refresh();
  }, 1000);
});

onUnmounted(() => {
  window.clearInterval(timer);
});

// ── 状态文本与样式映射 ─────────────────────────────────────────

/**
 * 计算 Worker 当前状态的人类可读中文说明
 */
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

/**
 * 映射 Worker 状态到具体的样式枚举（用于控制微徽标背景色与圆点呼吸动画）
 */
function statusKey(worker: WorkerSnapshot): "disabled" | "reconnecting" | "flood" | "stopped" | "uploading" | "idle" {
  if (!worker.enabled) return "disabled";
  if (worker.reconnecting) return "reconnecting";
  if (worker.flood_wait_seconds > 0) return "flood";
  if (!worker.running || !worker.accepting) return "stopped";
  if (worker.in_flight > 0) return "uploading";
  return "idle";
}

// ── Worker 节点生命周期动作 ────────────────────────────────────

/** 禁用指定 Worker */
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

/** 启用指定 Worker */
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

/** 永久删除指定 Worker 凭证文件 */
async function onDelete(name: string): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `将永久删除 sessions/${name}.session 授权凭据，该操作不可撤回。正在处理的任务将自动释放回待发队列。`,
      `确定删除 ${name}？`,
      {
        type: "warning",
        confirmButtonText: "确认删除",
        cancelButtonText: "取消",
        confirmButtonClass: "el-button--danger",
      },
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
  <el-card shadow="never" class="worker-card">
    <template #header>
      <div class="head">
        <div class="title-wrap">
          <span>Worker 节点</span>
          <span v-if="workers.length" class="count-badge">{{ workers.length }}</span>
        </div>
        <el-button type="primary" size="small" @click="emit('add')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13" style="margin-right: 4px;">
            <line x1="12" y1="5" x2="12" y2="19"></line>
            <line x1="5" y1="12" x2="19" y2="12"></line>
          </svg>
          添加 Session
        </el-button>
      </div>
    </template>

    <!-- 错误异常提示 -->
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />

    <!-- 空状态：尚未挂载任何 Telegram Session 凭证 -->
    <div v-else-if="!workers.length" class="empty-state">
      <div class="empty-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="26" height="26">
          <rect x="2" y="7" width="20" height="14" rx="2" ry="2"/>
          <path d="M16 21V5a2 2 0 00-2-2h-4a2 2 0 00-2 2v16"/>
        </svg>
      </div>
      <p class="empty-title">暂无已挂载 Worker</p>
      <p class="empty-desc">点击上方按钮添加 Telegram Session 授权账号</p>
    </div>

    <!-- Worker 节点卡片列表 -->
    <div v-else class="list">
      <div v-for="worker in workers" :key="worker.name" class="item">
        <div class="row">
          <div class="worker-info">
            <strong class="worker-name">{{ worker.name }}</strong>
            <span v-if="worker.username" class="user">@{{ worker.username }}</span>
          </div>

          <!-- 自定义精密状态微徽标 (带呼吸光点) -->
          <div class="status-badge" :class="statusKey(worker)">
            <span class="status-dot" :class="statusKey(worker)"></span>
            <span class="status-label">{{ statusLabel(worker) }}</span>
          </div>
        </div>

        <!-- 队列与并发在传数据格 -->
        <div class="metrics-grid">
          <div class="metric-cell">
            <span class="m-label">队列积压</span>
            <span class="m-val">{{ worker.queue_size }}</span>
          </div>
          <div class="metric-divider"></div>
          <div class="metric-cell">
            <span class="m-label">并发在传</span>
            <span class="m-val" :class="{ 'm-active': worker.in_flight > 0 }">{{ worker.in_flight }}</span>
          </div>
        </div>

        <!-- 重连异常原因报错 -->
        <div v-if="worker.reconnecting && worker.reconnect_error" class="err">
          {{ worker.reconnect_error }}
        </div>

        <!-- 操作按钮工具栏 -->
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
            plain
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

.title-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
}

.count-badge {
  font-size: 11px;
  font-weight: 500;
  color: var(--text-secondary);
  background: var(--hover);
  padding: 1px 7px;
  border-radius: 9999px;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 36px 16px;
  text-align: center;
}

.empty-icon {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: #f0f5f2;
  color: #799480;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 12px;
}

.empty-title {
  margin: 0 0 4px;
  font-size: 13.5px;
  font-weight: 500;
  color: var(--text);
}

.empty-desc {
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

@media (max-width: 1100px) {
  .list {
    flex-direction: row;
    overflow-x: auto;
    scroll-snap-type: x mandatory;
    padding-bottom: 4px;
  }

  .item {
    min-width: 260px;
    scroll-snap-align: start;
  }
}

.item {
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: #ffffff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.02);
  transition: border-color 0.2s, box-shadow 0.2s;
}

.item:hover {
  border-color: rgba(0, 0, 0, 0.12);
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.04);
}

.row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 10px;
}

.worker-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.worker-name {
  font-size: 14px;
  color: var(--text);
  font-weight: 600;
}

.user {
  color: var(--text-secondary);
  font-size: 12px;
}

/* ── 微徽标 (Micro-Badge) ── */
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 7px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 500;
  background: #f4f7f5;
  color: var(--text-secondary);
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.status-badge.idle .status-dot {
  background: var(--ok);
}

.status-badge.uploading {
  background: var(--accent-soft);
  color: var(--accent);
}

.status-badge.uploading .status-dot {
  background: var(--accent);
  animation: pulse-glow 2s infinite ease-in-out;
}

.status-badge.reconnecting,
.status-badge.flood {
  background: #fdf5ea;
  color: var(--warn);
}

.status-badge.reconnecting .status-dot,
.status-badge.flood .status-dot {
  background: var(--warn);
  animation: pulse-glow-warn 2s infinite ease-in-out;
}

.status-badge.stopped {
  background: #fdf2f2;
  color: var(--bad);
}

.status-badge.stopped .status-dot {
  background: var(--bad);
}

.status-badge.disabled .status-dot {
  background: #b5c2b7;
}

/* ── 指标小栅格 ── */
.metrics-grid {
  display: flex;
  align-items: center;
  margin-top: 10px;
  padding: 6px 10px;
  background: #f8faf8;
  border-radius: 6px;
  border: 1px solid var(--border-light);
}

.metric-cell {
  flex: 1;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}

.metric-divider {
  width: 1px;
  height: 14px;
  background: var(--border);
  margin: 0 12px;
}

.m-label {
  font-size: 11px;
  color: var(--text-secondary);
}

.m-val {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.m-val.m-active {
  color: var(--accent);
}

.err {
  margin-top: 6px;
  padding: 5px 8px;
  background: #fdf5f5;
  border-radius: 6px;
  color: var(--bad);
  font-size: 11.5px;
  word-break: break-all;
}

.actions {
  margin-top: 10px;
  display: flex;
  gap: 8px;
}
</style>

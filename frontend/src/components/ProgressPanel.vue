<script setup lang="ts">
/**
 * @file ProgressPanel.vue
 * @description 实时文件传输进度面板 (Progress Panel)
 *
 * 核心设计与算法：
 * 1. 【SSE 实时推流】：通过 EventSource 订阅 `/api/progress/stream`，支持断线自动接收最新快照；
 * 2. 【滑动窗口测速 (Sliding-Window Speedometer)】：
 *    - 针对 Telethon 分片推送频率，记录最近 5 次采样的瞬时速率，取均值平滑突发毛刺；
 *    - 当时间跨度 $\Delta t \le 0.05s$ 时维持上次速度，防止除以极小时间导致速度数值狂飙；
 * 3. 【剩余时间预估 (ETA)】：基于平滑速度与剩余字节量动态估算，格式化为 HH:MM:SS 或 MM:SS；
 * 4. 【遥测上报】：通过 watch `summary` 计算全局并发总速度与进行中任务数，向父组件派发 `updateSummary`；
 * 5. 【终态自清理】：任务处于 success 或 failed 后，设置 4 秒渐隐倒计时，从活动列表中自动移除；
 * 6. 【5px 精密微轨视觉】：抛弃粗条纹流光，采用优雅纤细的微轨进度条。
 */

import { computed, onMounted, onUnmounted, reactive, watch } from "vue";
import { openProgressStream } from "../api";
import type { UploadProgress, ProgressStats } from "../types";

// ── 事件声明 ───────────────────────────────────────────────────

const emit = defineEmits<{
  /** 向父页面广播当前正在传输的总速率及在传任务数 */
  updateSummary: [summary: { totalSpeed: number; inFlightCount: number }];
}>();

// ── 内部响应式状态 ─────────────────────────────────────────────

/** 当前活跃任务的 Map（键为 task_id，值为 UploadProgress） */
const items = reactive(new Map<number, UploadProgress>());

/** 各任务前端测算的速度与 ETA 遥测数据缓存 */
const stats = reactive(new Map<number, ProgressStats>());

/** 转化为数组用于 v-for 列表渲染 */
const list = computed(() => Array.from(items.values()));

/** 终态清理定时器句柄集合（task_id -> setTimeout timer） */
const hideTimers = new Map<number, number>();

/** 上一次接收到进度的采样快照（task_id -> { current 字节, time 时间戳 }） */
const prevSnapshots = new Map<number, { current: number; time: number }>();

/** 滑动窗口速度历史队列（task_id -> 最近 5 次速度采样） */
const speedHistory = new Map<number, number[]>();

/** 原生 SSE EventSource 实例 */
let source: EventSource | null = null;

// ── 全局速率聚合与广播 ─────────────────────────────────────────

/** 实时统计当前所有正在上传任务的累计吞吐与任务数 */
const summary = computed(() => {
  let speed = 0;
  let uploading = 0;
  for (const item of items.values()) {
    if (item.stage === "uploading") {
      uploading++;
      speed += stats.get(item.task_id)?.speed ?? 0;
    }
  }
  return { totalSpeed: speed, inFlightCount: uploading };
});

// 监听 summary 变化即时向 MonitorPage 派发更新
watch(summary, (val) => {
  emit("updateSummary", val);
}, { immediate: true });

// ── 进度应用与测速核心算法 ─────────────────────────────────────

/**
 * 接收后端 SSE 传来的进度事件，执行动态测速、ETA 计算及视图更新
 */
function apply(progress: UploadProgress): void {
  const now = performance.now() / 1000;
  let speed = 0;

  if (progress.stage === "uploading") {
    const prev = prevSnapshots.get(progress.task_id);
    if (prev) {
      const dt = now - prev.time;
      const db = progress.current - prev.current;

      // 仅在时间跨度大于 50ms 且传输字节递增时重新采样，避免时间过短产生极端尖峰
      if (dt > 0.05 && db >= 0) {
        const instantSpeed = db / dt;
        const history = speedHistory.get(progress.task_id) ?? [];
        history.push(instantSpeed);

        // 滑动窗口保留最多 5 个采样样本
        if (history.length > 5) history.shift();
        speedHistory.set(progress.task_id, history);

        // 窗口平均平滑速度
        speed = history.reduce((a, b) => a + b, 0) / history.length;
      } else {
        // dt 过短时沿用上一次测得的速度
        const existing = stats.get(progress.task_id);
        speed = existing?.speed ?? 0;
      }
    }
    prevSnapshots.set(progress.task_id, { current: progress.current, time: now });
  }

  // 估算剩余时间 (ETA)
  const remaining = progress.total - progress.current;
  const eta = speed > 0 ? remaining / speed : -1;
  stats.set(progress.task_id, { speed, eta });

  items.set(progress.task_id, progress);

  // 成功或失败状态：延迟 4 秒自动移除
  if (progress.stage === "success" || progress.stage === "failed") {
    const existing = hideTimers.get(progress.task_id);
    if (existing) {
      window.clearTimeout(existing);
    }
    hideTimers.set(
      progress.task_id,
      window.setTimeout(() => {
        items.delete(progress.task_id);
        stats.delete(progress.task_id);
        hideTimers.delete(progress.task_id);
        prevSnapshots.delete(progress.task_id);
        speedHistory.delete(progress.task_id);
      }, 4000),
    );
  }
}

// ── 辅助工具函数 ───────────────────────────────────────────────

/** 映射任务阶段到 Element Plus 进度条状态颜色 */
function status(stage: UploadProgress["stage"]): "success" | "exception" | "warning" | undefined {
  if (stage === "success") return "success";
  if (stage === "failed") return "exception";
  if (stage === "flood_wait") return "warning";
  return undefined;
}

/** 阶段状态中文可读标签 */
function stageLabel(stage: UploadProgress["stage"]): string {
  if (stage === "uploading") return "上传中";
  if (stage === "success") return "已完成";
  if (stage === "failed") return "失败";
  if (stage === "flood_wait") return "限流";
  return stage;
}

/** 获取指定任务测算的统计数据 */
function getStats(taskId: number): ProgressStats {
  return stats.get(taskId) ?? { speed: 0, eta: -1 };
}

/** 格式化字节大小 */
function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

/** 格式化传输速率 (MB/s) */
function formatSpeed(bytesPerSec: number): string {
  if (bytesPerSec <= 0) return "--";
  return `${formatBytes(bytesPerSec)}/s`;
}

/** 格式化剩余时间 (分:秒 或 时:分:秒) */
function formatETA(seconds: number): string {
  if (seconds < 0) return "--:--";
  if (seconds > 3600) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${h}:${String(m).padStart(2, "0")}:00`;
  }
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

/** 格式化已传大小与总大小文本（针对相册聚合做容错） */
function transferred(item: UploadProgress): string {
  if (item.total <= 32 && item.total > 0 && item.current <= item.total) {
    return `${item.current.toFixed(1)} / ${item.total.toFixed(0)} files`;
  }
  return `${formatBytes(item.current)} / ${formatBytes(item.total)}`;
}

// ── 生命周期 ───────────────────────────────────────────────────

onMounted(() => {
  source = openProgressStream(apply);
});

onUnmounted(() => {
  // 组件卸载时关闭 SSE 长连接并清空全部残留的自清理计时器
  source?.close();
  for (const timer of hideTimers.values()) {
    window.clearTimeout(timer);
  }
});
</script>

<template>
  <el-card shadow="never" class="progress-card">
    <template #header>
      <div class="card-header">
        <span>上传进度</span>
        <span v-if="summary.inFlightCount > 0" class="active-badge">
          {{ summary.inFlightCount }} 任务在传
        </span>
      </div>
    </template>

    <!-- 空状态：暂无进行中的上传任务 -->
    <div v-if="list.length === 0" class="empty-state">
      <div class="empty-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="26" height="26">
          <path d="M12 3v12m0-12L8 7m4-4l4 4M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </div>
      <p class="empty-title">当前无进行中的上传任务</p>
      <p class="empty-desc">监听目录中出现新视频时将自动调度 Worker 上传</p>
    </div>

    <!-- 任务进度列表 -->
    <div v-else class="list">
      <div v-for="item in list" :key="item.task_id" class="item" :class="item.stage">
        <!-- 头部：完整文件名展示 + 状态微徽标 -->
        <div class="row">
          <div class="filename-wrap">
            <span class="filename" :title="item.file_name">{{ item.file_name }}</span>
          </div>
          <div class="stage-badge" :class="item.stage">
            <span class="dot" :class="item.stage"></span>
            <span class="stage-text">{{ item.worker_name }} · {{ stageLabel(item.stage) }}</span>
          </div>
        </div>

        <!-- 5px 精密轨道微条 -->
        <el-progress
          :percentage="item.percent"
          :status="status(item.stage)"
          :stroke-width="5"
          :show-text="false"
        />

        <!-- 元数据栏：已传容量 · 进度百分比 ── 传输速率 · ETA -->
        <div class="meta-row">
          <span class="transferred">{{ transferred(item) }} · {{ item.percent }}%</span>
          <span v-if="item.stage === 'uploading'" class="speed-eta">
            {{ formatSpeed(getStats(item.task_id).speed) }} · 剩余 {{ formatETA(getStats(item.task_id).eta) }}
          </span>
          <span v-else-if="item.stage === 'success'" class="speed-eta success-text">上传成功</span>
          <span v-else-if="item.stage === 'failed'" class="speed-eta fail-text">上传失败</span>
          <span v-else-if="item.stage === 'flood_wait'" class="speed-eta warn-text">触发限流等待</span>
        </div>

        <!-- 异常报错消息提示 -->
        <div v-if="item.message" class="msg">{{ item.message }}</div>
      </div>
    </div>
  </el-card>
</template>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.active-badge {
  font-size: 11.5px;
  font-weight: 500;
  color: var(--accent);
  background: var(--accent-soft);
  padding: 2px 8px;
  border-radius: 9999px;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 42px 20px;
  text-align: center;
}

.empty-icon {
  width: 48px;
  height: 48px;
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
  font-size: 14px;
  font-weight: 500;
  color: var(--text);
}

.empty-desc {
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
  max-width: 280px;
  line-height: 1.5;
}

.list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.item {
  padding: 13px 16px;
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
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.filename-wrap {
  min-width: 0;
  flex: 1;
}

.filename {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text);
}

.stage-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 500;
  color: var(--text-secondary);
  background: #f5f8f6;
  padding: 3px 8px;
  border-radius: 6px;
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #a2b3a5;
}

.dot.uploading {
  background: var(--accent);
  animation: pulse-glow 2s infinite ease-in-out;
}

.dot.success {
  background: var(--ok);
}

.dot.failed {
  background: var(--bad);
}

.dot.flood_wait {
  background: var(--warn);
  animation: pulse-glow-warn 2s infinite ease-in-out;
}

.meta-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-secondary);
}

.transferred {
  font-variant-numeric: tabular-nums;
  font-size: 12px;
}

.speed-eta {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  font-weight: 500;
  font-size: 12px;
}

.success-text {
  color: var(--ok);
}

.fail-text {
  color: var(--bad);
}

.warn-text {
  color: var(--warn);
}

.msg {
  margin-top: 6px;
  padding: 5px 8px;
  background: #fdf5f5;
  border-radius: 6px;
  color: var(--bad);
  font-size: 11.5px;
  word-break: break-all;
}
</style>

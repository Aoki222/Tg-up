<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive } from "vue";
import { openProgressStream } from "../api";
import type { UploadProgress } from "../types";

const items = reactive(new Map<number, UploadProgress>());
const list = computed(() => Array.from(items.values()));
const hideTimers = new Map<number, number>();
let source: EventSource | null = null;

function apply(progress: UploadProgress): void {
  items.set(progress.task_id, progress);
  if (progress.stage === "success" || progress.stage === "failed") {
    const existing = hideTimers.get(progress.task_id);
    if (existing) {
      window.clearTimeout(existing);
    }
    hideTimers.set(
      progress.task_id,
      window.setTimeout(() => {
        items.delete(progress.task_id);
        hideTimers.delete(progress.task_id);
      }, 4000),
    );
  }
}

function status(stage: UploadProgress["stage"]): "success" | "exception" | "warning" | undefined {
  if (stage === "success") {
    return "success";
  }
  if (stage === "failed") {
    return "exception";
  }
  if (stage === "flood_wait") {
    return "warning";
  }
  return undefined;
}

onMounted(() => {
  source = openProgressStream(apply);
});

onUnmounted(() => {
  source?.close();
  for (const timer of hideTimers.values()) {
    window.clearTimeout(timer);
  }
});
</script>

<template>
  <el-card shadow="never">
    <template #header>上传进度</template>
    <el-empty v-if="list.length === 0" description="等待上传…" :image-size="72" />
    <div v-else class="list">
      <div v-for="item in list" :key="item.task_id" class="item">
        <div class="row">
          <strong>{{ item.file_name }}</strong>
          <span class="stage">{{ item.worker_name }} · {{ item.stage }}</span>
        </div>
        <el-progress
          :percentage="item.percent"
          :status="status(item.stage)"
          :stroke-width="12"
          striped
          striped-flow
        />
        <div v-if="item.message" class="msg">{{ item.message }}</div>
      </div>
    </div>
  </el-card>
</template>

<style scoped>
.list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.item {
  padding: 12px 14px 10px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: #fff;
}
.row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
  font-size: 14px;
}
.stage {
  color: var(--accent);
  font-size: 12px;
  white-space: nowrap;
}
.msg {
  margin-top: 6px;
  color: var(--muted);
  font-size: 12px;
}
</style>

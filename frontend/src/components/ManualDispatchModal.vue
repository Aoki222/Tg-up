<script setup lang="ts">
/**
 * @file ManualDispatchModal.vue
 * @description 手动投递任务专属模态弹窗 (Manual Dispatch Modal)
 *
 * 核心交互与架构：
 * 1. 【专属复选目录树】基于 el-tree show-checkbox，仅负责按需挑选文件，支持“仅看未上传/未命中”过滤；
 * 2. 【双分支提交模式】
 *    - 直接提交（默认）：按各文件所属目录的已有路由规则一键快速入库；
 *    - 自定义参数覆盖：支持动态覆盖目标群组 (chat_id)、话题 (topic_id)、文案 (caption)、成功后行为与视频预览；
 * 3. 【API 联动】调用 POST /api/tasks/dispatch，支持多文件批量入库，返回详尽的 dispatched / skipped 报告。
 */

import { computed, ref, watch } from "vue";
import { ElMessage, ElNotification } from "element-plus";
import { useQueryClient } from "@tanstack/vue-query";
import {
  dispatchManualTasks,
  fetchFsNodes,
  fetchSettings,
} from "../api";
import type {
  ChatAliasItem,
  FsNode,
  ManualDispatchPayload,
  ManualDispatchResult,
} from "../types";
import { formatBytes } from "../format";

const props = defineProps<{
  /** 控制弹窗显隐 */
  modelValue: boolean;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: boolean];
  dispatched: [result: ManualDispatchResult];
}>();

const queryClient = useQueryClient();

// ── 响应式状态 ─────────────────────────────────────────────────
const loading = ref(false);
const dispatching = ref(false);

const watchRoots = ref<string[]>([]);
const configuredChats = ref<ChatAliasItem[]>([]);
const treeRef = ref<any>(null);

// 目录树搜索与过滤
const filterText = ref("");
const onlyUnfinished = ref(true);

// 选中状态
const selectedFiles = ref<FsNode[]>([]);

// 提交模式：'default' 直接提交 | 'custom' 自定义参数覆盖
const dispatchMode = ref<"default" | "custom">("default");

// 自定义覆盖表单
const targetChatId = ref<number | null>(null);
const targetTopicId = ref<number | null>(null);
const customCaption = ref("");
const afterSuccess = ref<"none" | "delete" | "archive">("none");
const needPreview = ref<boolean | null>(null);

// ── 统计计算 ───────────────────────────────────────────────────

const totalSelectedBytes = computed(() =>
  selectedFiles.value.reduce((acc, item) => acc + (item.size || 0), 0),
);

const hasUnmatchedSelected = computed(() =>
  selectedFiles.value.some((item) => item.task_status === "unmatched" || !item.current_route?.matched),
);

// ── 弹窗打开初始化 ─────────────────────────────────────────────

watch(
  () => props.modelValue,
  async (open) => {
    if (!open) {
      resetState();
      return;
    }
    await loadInitialConfig();
  },
);

async function loadInitialConfig(): Promise<void> {
  loading.value = true;
  try {
    const settings = await fetchSettings();
    watchRoots.value = (settings.observer_roots || []).map((r) => r.path);
    configuredChats.value = settings.chats || [];
    if (configuredChats.value.length > 0 && targetChatId.value == null) {
      targetChatId.value = configuredChats.value[0].chat_id;
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : "读取监控配置失败");
  } finally {
    loading.value = false;
  }
}

function resetState(): void {
  selectedFiles.value = [];
  filterText.value = "";
  dispatchMode.value = "default";
  targetTopicId.value = null;
  customCaption.value = "";
  afterSuccess.value = "none";
  needPreview.value = null;
}

function closeModal(): void {
  emit("update:modelValue", false);
}

// ── 目录树节点懒加载 ───────────────────────────────────────────

async function loadFsTreeNode(node: any, resolve: (data: FsNode[]) => void): Promise<void> {
  if (node.level === 0) {
    // 顶级节点：返回已配置的各物理监控根目录
    const rootNodes: FsNode[] = watchRoots.value.map((path) => {
      const parts = path.replace(/\\/g, "/").split("/").filter(Boolean);
      const name = parts[parts.length - 1] || path;
      return {
        path,
        name,
        is_dir: true,
        has_children: true,
        is_root: true,
        supported_ext: true,
      };
    });
    resolve(rootNodes);
    return;
  }

  const currentPath = (node.data as FsNode).path;
  try {
    const items = await queryClient.fetchQuery({
      queryKey: ["fs-nodes", currentPath],
      queryFn: () => fetchFsNodes(currentPath),
    });
    resolve(items);
  } catch {
    resolve([]);
  }
}

// 节点是否禁用勾选（正在上传中或排队中的任务不允许重复勾选投递）
function isNodeDisabled(data: FsNode): boolean {
  if (data.is_dir) return false;
  return data.task_status === "uploading" || data.task_status === "pending" || data.task_status === "preparing";
}

// 复选框变动触发
function handleCheckChange(): void {
  if (!treeRef.value) return;
  const checkedNodes: FsNode[] = treeRef.value.getCheckedNodes(false, false);
  // 仅筛选叶子文件，过滤掉文件夹
  selectedFiles.value = checkedNodes.filter((n) => !n.is_dir);
}

function removeSelectedFile(path: string): void {
  selectedFiles.value = selectedFiles.value.filter((f) => f.path !== path);
  if (treeRef.value) {
    treeRef.value.setChecked(path, false, false);
  }
}

function clearAllSelected(): void {
  selectedFiles.value = [];
  if (treeRef.value) {
    treeRef.value.setCheckedNodes([]);
  }
}

async function refreshTree(): Promise<void> {
  await queryClient.invalidateQueries({ queryKey: ["fs-nodes"] });
  if (treeRef.value?.store) {
    treeRef.value.store.root.loaded = false;
    treeRef.value.store.root.loadChildren();
  }
  clearAllSelected();
  ElMessage.success("目录结构已刷新");
}

// 过滤搜索判定
function filterNode(value: string, data: FsNode): boolean {
  if (!value) {
    if (onlyUnfinished.value && !data.is_dir) {
      return data.task_status !== "success";
    }
    return true;
  }
  const matchText = data.name.toLowerCase().includes(value.toLowerCase());
  if (onlyUnfinished.value && !data.is_dir) {
    return matchText && data.task_status !== "success";
  }
  return matchText;
}

watch([filterText, onlyUnfinished], () => {
  treeRef.value?.filter(filterText.value);
});

// ── 提交手动投递 ───────────────────────────────────────────────

async function handleDispatch(): Promise<void> {
  if (selectedFiles.value.length === 0) {
    ElMessage.warning("请至少勾选一个文件进行投递");
    return;
  }

  // 校验：如果选择直接提交，但包含未命中任何规则的文件，且没有配置默认群
  if (dispatchMode.value === "default" && hasUnmatchedSelected.value) {
    ElMessage.error("选中的文件中含有「未命中规则」的文件，请切换为「自定义参数」指定目标群组后提交");
    dispatchMode.value = "custom";
    return;
  }

  const paths = selectedFiles.value.map((f) => f.path);
  const payload: ManualDispatchPayload = {
    file_paths: paths,
  };

  if (dispatchMode.value === "custom") {
    if (targetChatId.value != null) {
      payload.chat_id = targetChatId.value;
    }
    if (targetTopicId.value != null) {
      payload.topic_id = targetTopicId.value;
    }
    if (customCaption.value.trim()) {
      payload.caption = customCaption.value.trim();
    }
    if (afterSuccess.value !== "none") {
      payload.after_success = afterSuccess.value;
    }
    if (needPreview.value !== null) {
      payload.need_preview = needPreview.value;
    }
  }

  dispatching.value = true;
  try {
    const res = await dispatchManualTasks(payload);
    if (res.dispatched_count > 0) {
      ElMessage.success(`成功投递 ${res.dispatched_count} 个文件至上传队列`);
    }

    if (res.skipped_count > 0) {
      const skipReasons = res.skipped.map((s) => `${s.file_path}: ${s.reason || "跳过"}`).join("\n");
      ElNotification({
        title: `${res.skipped_count} 个文件被跳过`,
        message: skipReasons,
        type: "warning",
        duration: 5000,
      });
    }

    await queryClient.invalidateQueries({ queryKey: ["fs-nodes"] });
    emit("dispatched", res);
    closeModal();
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : "手动投递失败");
  } finally {
    dispatching.value = false;
  }
}
</script>

<template>
  <div v-show="modelValue" class="dispatch-layer" @click.self="closeModal">
    <div
      class="dispatch-modal"
      role="dialog"
      aria-label="手动投递任务"
      @click.stop
    >
      <!-- ── 弹窗顶栏 ── -->
      <header class="dispatch-head">
        <div class="head-lead">
          <div class="head-icon-box">🚀</div>
          <div>
            <h2 class="head-title">手动投递任务 (Manual Dispatch)</h2>
            <p class="head-sub">
              勾选需要上传的文件，立即派发至队列 · 支持直接提交或自定义目标群组/文案覆盖
            </p>
          </div>
        </div>
        <button
          type="button"
          class="head-close-btn"
          aria-label="关闭"
          @click="closeModal"
        >
          ✕
        </button>
      </header>

      <!-- ── 弹窗主体双栏 ── -->
      <div v-loading="loading" class="dispatch-body">
        <!-- ══════════════════════════════════════════════════════
             左栏：带复选框的监控目录文件树
        ══════════════════════════════════════════════════════ -->
        <section class="tree-panel">
          <div class="tree-toolbar">
            <input
              v-model="filterText"
              type="text"
              class="tree-search-input"
              placeholder="🔍 搜索文件名..."
            />

            <label class="unfinished-filter-chip" :class="{ 'is-active': onlyUnfinished }">
              <input v-model="onlyUnfinished" type="checkbox" class="sr-only" />
              <span class="chip-dot"></span>
              <span>仅看未上传/未命中</span>
            </label>

            <button
              type="button"
              class="tree-action-btn font-mono"
              title="重新加载目录结构"
              @click="refreshTree"
            >
              🔄 刷新
            </button>
          </div>

          <div class="tree-container">
            <el-tree
              ref="treeRef"
              lazy
              show-checkbox
              node-key="path"
              :load="loadFsTreeNode"
              :props="{ label: 'name', isLeaf: (data: FsNode) => !data.is_dir, disabled: isNodeDisabled }"
              :filter-node-method="filterNode"
              class="dispatch-el-tree"
              @check-change="handleCheckChange"
            >
              <template #default="{ data }">
                <div class="tree-row" :class="{ 'is-disabled-row': isNodeDisabled(data) }">
                  <span class="row-icon">{{ data.is_dir ? "📂" : "📄" }}</span>
                  <span
                    class="row-name truncate"
                    :class="{ 'is-folder': data.is_dir }"
                    :title="data.path"
                  >
                    {{ data.name }}
                  </span>

                  <!-- 文件属性与状态胶囊 -->
                  <template v-if="!data.is_dir">
                    <span class="row-size font-mono">{{ formatBytes(data.size || 0) }}</span>

                    <!-- 状态徽章 -->
                    <span
                      v-if="data.task_status === 'uploading'"
                      class="row-badge status-uploading"
                    >
                      上传中
                    </span>
                    <span
                      v-else-if="data.task_status === 'success'"
                      class="row-badge status-success"
                    >
                      已上传
                    </span>
                    <span
                      v-else-if="data.task_status === 'pending' || data.task_status === 'preparing'"
                      class="row-badge status-pending"
                    >
                      排队中
                    </span>
                    <span
                      v-else-if="data.task_status === 'failed'"
                      class="row-badge status-failed"
                      :title="data.task_error || '失败'"
                    >
                      失败
                    </span>
                    <span
                      v-else-if="data.task_status === 'unmatched' || !data.current_route?.matched"
                      class="row-badge status-unmatched"
                    >
                      ⚠️ 未命中
                    </span>
                    <span v-else class="row-badge status-ready">待上传</span>

                    <!-- 匹配规则展示 -->
                    <span
                      v-if="data.current_route?.matched"
                      class="row-route-badge font-mono"
                      :title="data.current_route?.dest_name || ''"
                    >
                      {{ data.current_route?.dest_name }}
                    </span>
                  </template>
                </div>
              </template>
            </el-tree>
          </div>
        </section>

        <!-- ══════════════════════════════════════════════════════
             右栏：投递控制台与参数微调面板
        ══════════════════════════════════════════════════════ -->
        <aside class="console-panel">
          <!-- 已选择清单概览卡片 -->
          <div class="summary-card" :class="{ 'has-selection': selectedFiles.length > 0 }">
            <div class="summary-lead">
              <div>
                <strong class="summary-count font-mono">
                  已选 {{ selectedFiles.length }} 个文件
                </strong>
                <span class="summary-size font-mono">
                  (共 {{ formatBytes(totalSelectedBytes) }})
                </span>
              </div>
              <button
                v-if="selectedFiles.length > 0"
                type="button"
                class="summary-clear-btn"
                title="清空已选择文件"
                @click="clearAllSelected"
              >
                清空
              </button>
            </div>

            <!-- 已选文件微型列表 -->
            <div v-if="selectedFiles.length > 0" class="selected-mini-list">
              <div
                v-for="item in selectedFiles"
                :key="item.path"
                class="mini-item"
                :title="item.path"
              >
                <span class="mini-name truncate">{{ item.name }}</span>
                <span class="mini-size font-mono">{{ formatBytes(item.size || 0) }}</span>
                <button
                  type="button"
                  class="mini-remove-btn"
                  title="移除此项"
                  @click="removeSelectedFile(item.path)"
                >
                  ✕
                </button>
              </div>
            </div>
            <p v-else class="summary-empty-tip">
              👈 请在左侧目录树中勾选想要投递上传的文件
            </p>
          </div>

          <!-- 模式切换分段控制器 (Segmented Control) -->
          <div class="mode-tabs">
            <button
              type="button"
              class="tab-btn"
              :class="{ 'is-active': dispatchMode === 'default' }"
              @click="dispatchMode = 'default'"
            >
              直接提交 (默认规则)
            </button>
            <button
              type="button"
              class="tab-btn"
              :class="{ 'is-active': dispatchMode === 'custom' }"
              @click="dispatchMode = 'custom'"
            >
              自定义参数覆盖
            </button>
          </div>

          <!-- 模式 1：默认说明 -->
          <div v-if="dispatchMode === 'default'" class="default-mode-hint">
            <p class="hint-title">⚡ 极速自动模式</p>
            <p class="hint-desc">
              所选文件将自动按照各自所在目录的规则配置（目标频道、话题、文案模板）直接分发上传。
            </p>
            <div v-if="hasUnmatchedSelected" class="unmatched-warn-box">
              ⚠️ 选中的文件包含未命中任何路由规则的项。请切换至「自定义参数覆盖」指定目标群组。
            </div>
          </div>

          <!-- 模式 2：自定义参数覆盖表单 -->
          <div v-else class="custom-form-body">
            <!-- 目标群组 -->
            <div class="form-field">
              <label class="form-label">
                目标群组 / 频道 (chat_id)
                <span class="text-danger">*</span>
              </label>
              <select v-model="targetChatId" class="app-select">
                <option :value="null">-- 请选择已绑定的目标群组 --</option>
                <option
                  v-for="chat in configuredChats"
                  :key="chat.chat_id"
                  :value="chat.chat_id"
                >
                  📣 {{ chat.alias || chat.title || chat.chat_id }} ({{ chat.chat_id }})
                </option>
              </select>
            </div>

            <!-- 话题 ID -->
            <div class="form-field">
              <label class="form-label">指定话题 ID (topic_id，可选)</label>
              <input
                v-model.number="targetTopicId"
                type="number"
                class="app-input font-mono"
                placeholder="留空则按所属目录规则自动决议"
              />
            </div>

            <!-- 附加文案 -->
            <div class="form-field">
              <label class="form-label">附加说明文案 (caption，可选)</label>
              <textarea
                v-model="customCaption"
                class="app-textarea"
                rows="2"
                placeholder="留空则自动按全局宏模板生成，支持 #标签"
              ></textarea>
            </div>

            <!-- 成功后处理 -->
            <div class="form-field">
              <label class="form-label">上传成功后本地源文件操作</label>
              <div class="radio-row">
                <label class="radio-label">
                  <input v-model="afterSuccess" type="radio" value="none" />
                  <span>保留本地文件</span>
                </label>
                <label class="radio-label">
                  <input v-model="afterSuccess" type="radio" value="delete" />
                  <span class="text-danger">上传后删除</span>
                </label>
              </div>
            </div>

            <!-- 视频预览抽帧 -->
            <div class="form-field-switch">
              <div>
                <strong class="switch-title">生成视频封面与九宫格预览</strong>
                <span class="switch-desc">关闭则不提取首帧与拼图，提升处理速度</span>
              </div>
              <el-switch
                :model-value="needPreview ?? true"
                size="small"
                @change="needPreview = Boolean($event)"
              />
            </div>
          </div>

          <!-- 底部控制按钮 -->
          <footer class="console-footer">
            <button
              type="button"
              class="footer-cancel-btn"
              :disabled="dispatching"
              @click="closeModal"
            >
              取消
            </button>
            <button
              type="button"
              class="footer-submit-btn"
              :disabled="selectedFiles.length === 0 || dispatching"
              @click="handleDispatch"
            >
              <span v-if="dispatching">正在投递...</span>
              <span v-else>
                🚀 立即投递至上传队列 ({{ selectedFiles.length }})
              </span>
            </button>
          </footer>
        </aside>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ══════════════════════════════════════════════════════════
   模态弹窗外层蒙层与居中容器
══════════════════════════════════════════════════════════ */
.dispatch-layer {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 18px;
  background: rgba(18, 28, 22, 0.42);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}

.dispatch-modal {
  display: flex;
  flex-direction: column;
  width: min(880px, 96vw);
  max-height: min(88dvh, 720px);
  height: min(88dvh, 720px);
  background: var(--surface, #ffffff);
  border-radius: var(--radius-xl, 16px);
  border: 1px solid var(--border, rgba(0, 0, 0, 0.08));
  box-shadow: 0 24px 64px -12px rgba(18, 30, 20, 0.24);
  overflow: hidden;
}

/* ── 顶栏 ── */
.dispatch-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 20px;
  border-bottom: 1px solid var(--border-light, rgba(0, 0, 0, 0.06));
  background: #ffffff;
  flex-shrink: 0;
}

.head-lead {
  display: flex;
  align-items: center;
  gap: 12px;
}

.head-icon-box {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: var(--radius-md, 10px);
  background: rgba(40, 153, 90, 0.1);
  font-size: 16px;
  flex-shrink: 0;
}

.head-title {
  margin: 0;
  font-size: 16.5px;
  font-weight: 700;
  color: var(--text, #111827);
  letter-spacing: -0.01em;
}

.head-sub {
  margin: 2px 0 0;
  font-size: 12.5px;
  color: var(--text-secondary, #6b7280);
}

.head-close-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: var(--radius-sm, 6px);
  border: 0;
  background: #f3f4f6;
  color: var(--text-secondary, #6b7280);
  font-size: 14px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.head-close-btn:hover {
  background: #e5e7eb;
  color: var(--text, #111827);
}

/* ── 主体双栏 ── */
.dispatch-body {
  display: flex;
  flex: 1;
  min-height: 0;
  gap: 12px;
  padding: 14px 18px;
  background: #fbfcfb;
}

/* ── 左栏：复选目录树面板 ── */
.tree-panel {
  flex: 1.35;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: #ffffff;
  border: 1px solid var(--border, rgba(0, 0, 0, 0.08));
  border-radius: var(--radius-lg, 14px);
  overflow: hidden;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.02);
}

.tree-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border-light, rgba(0, 0, 0, 0.05));
  background: #ffffff;
  flex-shrink: 0;
}

.tree-search-input {
  flex: 1;
  min-width: 0;
  padding: 6px 10px;
  border: 1px solid var(--border, rgba(0, 0, 0, 0.1));
  border-radius: var(--radius-sm, 6px);
  font-size: 12.5px;
  background: #f9fafb;
  outline: none;
  transition: border-color 0.15s ease;
}

.tree-search-input:focus {
  border-color: var(--accent, #28995a);
  background: #ffffff;
}

.unfinished-filter-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 10px;
  border-radius: var(--radius-sm, 6px);
  border: 1px solid rgba(0, 0, 0, 0.08);
  background: #f9fafb;
  color: var(--text-secondary, #6b7280);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  user-select: none;
  transition: all 0.15s ease;
  white-space: nowrap;
}

.unfinished-filter-chip.is-active {
  border-color: rgba(40, 153, 90, 0.3);
  background: rgba(40, 153, 90, 0.08);
  color: #15803d;
  font-weight: 600;
}

.chip-dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full, 9999px);
  background: #9ca3af;
}

.unfinished-filter-chip.is-active .chip-dot {
  background: #10b981;
}

.tree-action-btn {
  padding: 5px 10px;
  border-radius: var(--radius-sm, 6px);
  border: 1px solid rgba(0, 0, 0, 0.08);
  background: #ffffff;
  color: var(--text-secondary, #6b7280);
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
}

.tree-action-btn:hover {
  background: #f3f4f6;
  color: var(--text, #111827);
}

.tree-container {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 8px;
}

.dispatch-el-tree {
  background: transparent !important;
  font-size: 13px;
}

.dispatch-el-tree :deep(.el-tree-node__content) {
  height: auto;
  min-height: 30px;
  padding: 3px 0;
  border-radius: var(--radius-sm, 6px);
  transition: background 0.12s ease;
}

.dispatch-el-tree :deep(.el-tree-node__content:hover) {
  background: rgba(0, 0, 0, 0.04);
}

.tree-row {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  min-width: 0;
  padding-right: 6px;
}

.tree-row.is-disabled-row {
  opacity: 0.55;
}

.row-icon {
  font-size: 13.5px;
  flex-shrink: 0;
}

.row-name {
  font-size: 13px;
  color: #1f2937;
  flex: 1;
  min-width: 0;
}

.row-name.is-folder {
  font-weight: 600;
  color: #111827;
}

.row-size {
  font-size: 11.5px;
  color: #9ca3af;
  flex-shrink: 0;
}

.row-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 6px;
  border-radius: var(--radius-sm, 6px);
  flex-shrink: 0;
  white-space: nowrap;
}

.status-uploading {
  background: rgba(40, 153, 90, 0.1);
  color: #28995a;
}

.status-success {
  background: #ecfdf5;
  color: #059669;
}

.status-pending {
  background: #fef3c7;
  color: #b45309;
}

.status-failed {
  background: #fee2e2;
  color: #b91c1c;
}

.status-unmatched {
  background: #fef2f2;
  color: #dc2626;
  border: 1px solid rgba(239, 68, 68, 0.25);
}

.status-ready {
  background: #eff6ff;
  color: #2563eb;
}

.row-route-badge {
  font-size: 11px;
  padding: 2px 6px;
  border-radius: var(--radius-sm, 6px);
  background: #f3f4f6;
  color: #6b7280;
  max-width: 84px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── 右栏：控制台面板 ── */
.console-panel {
  flex: 1;
  min-width: 280px;
  max-width: 360px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  background: #ffffff;
  border: 1px solid var(--border, rgba(0, 0, 0, 0.08));
  border-radius: var(--radius-lg, 14px);
  padding: 12px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.02);
}

.summary-card {
  padding: 10px;
  border-radius: var(--radius-md, 10px);
  background: #f9fafb;
  border: 1px solid var(--border-light, rgba(0, 0, 0, 0.06));
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.summary-card.has-selection {
  background: rgba(40, 153, 90, 0.04);
  border-color: rgba(40, 153, 90, 0.25);
}

.summary-lead {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.summary-count {
  font-size: 13.5px;
  font-weight: 700;
  color: var(--accent, #28995a);
}

.summary-size {
  font-size: 12.5px;
  color: #4b5563;
  margin-left: 4px;
}

.summary-clear-btn {
  border: 0;
  background: transparent;
  color: #9ca3af;
  font-size: 12px;
  cursor: pointer;
}

.summary-clear-btn:hover {
  color: #dc2626;
}

.selected-mini-list {
  max-height: 80px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding-right: 2px;
}

.mini-item {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 6px;
  background: #ffffff;
  border-radius: 4px;
  font-size: 12px;
  border: 1px solid rgba(0, 0, 0, 0.05);
}

.mini-name {
  flex: 1;
  min-width: 0;
  color: #374151;
}

.mini-size {
  font-size: 11px;
  color: #9ca3af;
}

.mini-remove-btn {
  border: 0;
  background: transparent;
  color: #9ca3af;
  font-size: 11px;
  cursor: pointer;
  padding: 0 2px;
}

.mini-remove-btn:hover {
  color: #ef4444;
}

.summary-empty-tip {
  margin: 0;
  font-size: 12.5px;
  color: var(--text-secondary, #6b7280);
}

/* 模式切换分段按钮 */
.mode-tabs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 3px;
  padding: 2.5px;
  border-radius: var(--radius-md, 10px);
  background: #f3f4f6;
  flex-shrink: 0;
}

.tab-btn {
  padding: 7px 6px;
  border-radius: var(--radius-sm, 6px);
  border: 0;
  background: transparent;
  color: var(--text-secondary, #6b7280);
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
  white-space: nowrap;
}

.tab-btn.is-active {
  background: #ffffff;
  color: var(--text, #111827);
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}

/* 默认模式提示 */
.default-mode-hint {
  padding: 10px;
  background: #f9fafb;
  border-radius: var(--radius-md, 10px);
  border: 1px solid var(--border-light, rgba(0, 0, 0, 0.05));
  font-size: 12.5px;
  flex: 1;
}

.hint-title {
  margin: 0 0 4px;
  font-weight: 600;
  color: var(--accent, #28995a);
}

.hint-desc {
  margin: 0;
  font-size: 12px;
  color: #6b7280;
  line-height: 1.4;
}

.unmatched-warn-box {
  margin-top: 8px;
  padding: 7px 10px;
  background: #fef2f2;
  border: 1px solid rgba(239, 68, 68, 0.2);
  border-radius: 6px;
  color: #b91c1c;
  font-size: 12px;
  line-height: 1.35;
}

/* 自定义表单 */
.custom-form-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-right: 2px;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.form-label {
  font-size: 12.5px;
  font-weight: 600;
  color: #374151;
}

.app-select,
.app-input,
.app-textarea {
  width: 100%;
  padding: 7px 10px;
  border: 1px solid var(--border, rgba(0, 0, 0, 0.1));
  border-radius: var(--radius-sm, 6px);
  font-size: 12.5px;
  background: #ffffff;
  color: #111827;
  outline: none;
  transition: border-color 0.15s ease;
  box-sizing: border-box;
}

.app-select:focus,
.app-input:focus,
.app-textarea:focus {
  border-color: var(--accent, #28995a);
}

.radio-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.radio-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12.5px;
  color: #374151;
  cursor: pointer;
}

.form-field-switch {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 8px;
  background: #f9fafb;
  border-radius: var(--radius-sm, 6px);
  gap: 8px;
}

.switch-title {
  display: block;
  font-size: 12.5px;
  color: #374151;
}

.switch-desc {
  display: block;
  font-size: 11.5px;
  color: #9ca3af;
}

/* 控制台底部动作区 */
.console-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-top: 10px;
  border-top: 1px solid var(--border-light, rgba(0, 0, 0, 0.06));
  flex-shrink: 0;
}

.footer-cancel-btn {
  padding: 8px 14px;
  border-radius: var(--radius-md, 10px);
  border: 1px solid rgba(0, 0, 0, 0.1);
  background: #ffffff;
  color: #4b5563;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.footer-cancel-btn:hover:not(:disabled) {
  background: #f3f4f6;
  color: #111827;
}

.footer-submit-btn {
  flex: 1;
  padding: 8px 16px;
  border-radius: var(--radius-md, 10px);
  border: 0;
  background: var(--accent, #28995a);
  color: #ffffff;
  font-size: 13.5px;
  font-weight: 700;
  cursor: pointer;
  box-shadow: 0 2px 6px rgba(40, 153, 90, 0.25);
  transition: all 0.16s cubic-bezier(0.16, 1, 0.3, 1);
  white-space: nowrap;
  user-select: none;
}

.footer-submit-btn:hover:not(:disabled) {
  background: #22834d;
  box-shadow: 0 4px 12px rgba(40, 153, 90, 0.35);
}

.footer-submit-btn:disabled,
.footer-cancel-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.text-danger {
  color: #dc2626;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  border: 0;
}
</style>

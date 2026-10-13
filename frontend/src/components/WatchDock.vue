<script setup lang="ts">
import { formatBytes } from "../format";
import type { FsNode } from "../types";
import { useWatchDock } from "../composables/useWatchDock";

defineProps<{
  open: boolean;
  width?: number;
  isResizing?: boolean;
}>();

const emit = defineEmits<{
  close: [];
  "open-policy": [path: string];
  "open-manual": [];
}>();

const {
  watchEnabled,
  togglingGlobalWatch,
  togglingRoots,
  togglingRoutes,
  expandedPaths,
  treeEpoch,
  dockItems,
  activeRootsCount,
  bumpTrees,
  setTreeRef,
  isCardExpanded,
  toggleCardExpand,
  loadCardFsNodes,
  refreshCardRoot,
  refreshCardFsNode,
  restoreSubfolderInheritance,
  loadWatchPaths,
  handleToggleGlobalWatch,
  handleToggleRoot,
  isSubfolderEnabled,
  handleToggleSubfolder,
  promptAddWatchPath,
  removeWatchPath,
  normPath,
} = useWatchDock();

defineExpose({ loadWatchPaths, bumpTrees });
</script>

<template>
  <aside
    class="dock"
    :class="{ shut: !open, 'is-resizing': isResizing }"
    :style="open && width ? { width: `${width}px` } : undefined"
  >
    <div class="dock-head">
      <div class="dock-head-left">
        <h2>监控目录</h2>
        <span class="dock-count-badge" :class="{ 'is-paused': !watchEnabled }">
          {{ !watchEnabled ? "全局已暂停" : `${activeRootsCount} 处活跃` }}
        </span>
      </div>

      <div class="dock-head-right">
        <button
          v-if="expandedPaths.size > 0"
          type="button"
          class="dock-collapse-all-btn font-mono"
          title="收起所有展开的子目录树"
          @click="expandedPaths.clear()"
        >
          全部收起
        </button>
        <button type="button" class="dock-toggle-btn" title="收起目录 (Ctrl+B)" @click="emit('close')">
          收起
        </button>
      </div>
    </div>

    <div class="dock-list">
      <p v-if="dockItems.length === 0" class="dock-empty">
        还没有监听目录，请点击下方添加
      </p>

      <article
        v-for="item in dockItems"
        :key="item.path"
        class="dock-item"
        :class="{
          'is-expanded': isCardExpanded(item.path),
          'is-root-paused': !item.enabled || !watchEnabled,
        }"
      >
        <div
          class="dock-row"
          :title="isCardExpanded(item.path) ? '点击折叠此目录文件树' : '点击展开此目录文件树'"
          @click="toggleCardExpand(item.path)"
        >
          <button
            type="button"
            class="dock-fold-arrow"
            :class="{ expanded: isCardExpanded(item.path) }"
            :title="isCardExpanded(item.path) ? '点击折叠此目录树' : '点击展开子文件树'"
            @click.stop="toggleCardExpand(item.path)"
          >
            {{ isCardExpanded(item.path) ? "▼" : "▶" }}
          </button>
          <span class="dock-dot" :class="{ bad: !item.ok }" :title="item.error || '可读取'"></span>
          <strong :title="item.path" class="dock-title-name">{{ item.name }}</strong>

          <div class="dock-actions" @click.stop>
            <el-switch
              :model-value="item.enabled"
              size="small"
              class="dock-root-switch"
              :disabled="togglingRoots.has(normPath(item.path))"
              :title="item.enabled ? '点击暂停此根目录监控' : '点击开启此根目录监控并执行补扫'"
              @change="handleToggleRoot(item.path)"
            />
            <button
              type="button"
              class="dock-policy-btn"
              title="配置此目录整体交付规则"
              @click="emit('open-policy', item.path)"
            >
              策略
            </button>
            <button
              type="button"
              class="dock-delete-btn"
              title="停止监听并从列表中移除此目录"
              @click="removeWatchPath(item.path)"
            >
              删除
            </button>
          </div>
        </div>

        <p class="dock-path font-mono" :title="item.path">{{ item.path }}</p>

        <div v-if="!isCardExpanded(item.path) && item.children.length" class="dock-children">
          <button
            v-for="child in item.children"
            :key="child.path"
            type="button"
            class="dock-child-chip"
            :class="{ 'is-child-disabled': !child.enabled }"
            :title="child.path"
            @click="emit('open-policy', child.path)"
          >
            {{ child.label }}{{ !child.enabled ? " (已阻断)" : "" }}
          </button>
        </div>

        <div v-if="isCardExpanded(item.path)" class="card-inline-tree-container" @click.stop>
          <div class="inline-tree-toolbar">
            <span class="inline-tree-hint font-mono">子目录与文件穿透</span>
            <button
              type="button"
              class="inline-refresh-btn font-mono"
              title="重新扫描并刷新此目录结构"
              @click="refreshCardRoot(item.path)"
            >
              🔄 刷新
            </button>
          </div>

          <el-tree
            :key="normPath(item.path) + ':' + treeEpoch"
            :ref="(el: unknown) => setTreeRef(item.path, el)"
            lazy
            :load="(node: any, resolve: any) => loadCardFsNodes(item.path, node, resolve)"
            node-key="path"
            :props="{ label: 'name', isLeaf: (data: FsNode) => !data.is_dir }"
            class="dock-fs-tree"
          >
            <template #default="{ node, data }">
              <div class="tree-node-row">
                <div class="node-left" :class="{ 'is-dir': data.is_dir }">
                  <span v-if="data.is_dir" class="node-icon">📂</span>
                  <span v-else class="node-icon">📄</span>
                  <span
                    class="node-name truncate"
                    :class="{ 'is-folder': data.is_dir }"
                    :title="data.path"
                  >
                    {{ data.name }}
                  </span>
                  <span
                    v-if="data.is_dir && !isSubfolderEnabled(data)"
                    class="node-route-badge blocked-rule"
                    title="此子目录监控已阻断，不会上传"
                  >
                    已阻断
                  </span>
                  <span
                    v-else-if="data.is_dir && data.current_route?.matched"
                    class="node-route-badge"
                    :class="{
                      'custom-rule': !data.current_route?.is_inherited,
                      'inherited-rule': data.current_route?.is_inherited,
                    }"
                    :title="data.current_route?.dest_name || ''"
                  >
                    {{ data.current_route?.is_inherited ? '继承' : '专属' }}: {{ data.current_route?.dest_name }}
                  </span>
                  <template v-if="!data.is_dir">
                    <span class="file-size font-mono">{{ formatBytes(data.size || 0) }}</span>
                    <span v-if="!data.supported_ext" class="file-status status-gray">未监听</span>
                    <span v-else-if="data.task_status === 'uploading'" class="file-status status-uploading">上传中</span>
                    <span v-else-if="data.task_status === 'success'" class="file-status status-success">已上传</span>
                    <span v-else-if="data.task_status === 'pending' || data.task_status === 'preparing'" class="file-status status-pending">排队中</span>
                    <span v-else-if="data.task_status === 'failed'" class="file-status status-failed" :title="data.task_error || '失败'">失败</span>
                    <span v-else-if="data.task_status === 'unmatched'" class="file-status status-unmatched">未命中</span>
                  </template>
                </div>

                <div v-if="data.is_dir" class="node-right shrink-0" @click.stop>
                  <el-switch
                    :model-value="isSubfolderEnabled(data)"
                    size="small"
                    class="tree-dir-switch"
                    :disabled="togglingRoutes.has(normPath(data.path))"
                    :title="isSubfolderEnabled(data) ? '点击阻断此子目录监控' : '点击恢复此子目录监控并补扫'"
                    @change="handleToggleSubfolder(data.path, node, data)"
                  />

                  <button
                    type="button"
                    class="tree-policy-btn"
                    title="为此子目录单独配置交付规则"
                    @click="emit('open-policy', data.path)"
                  >
                    策略
                  </button>
                  <button
                    v-if="data.current_route?.matched && !data.current_route?.is_inherited && !data.current_route?.auto_blocked && isSubfolderEnabled(data)"
                    type="button"
                    class="tree-inherit-btn"
                    title="清除专属规则，恢复跟随父级"
                    @click="restoreSubfolderInheritance(data.path)"
                  >
                    恢复继承
                  </button>
                  <button
                    type="button"
                    class="tree-refresh-icon-btn"
                    title="刷新此子目录"
                    @click="refreshCardFsNode(node, data)"
                  >
                    🔄
                  </button>
                </div>
              </div>
            </template>
          </el-tree>
        </div>
      </article>
    </div>

    <div class="dock-bottom-bar">
      <button
        type="button"
        class="dock-bottom-btn manual-dispatch-btn"
        title="打开手动投递窗口，勾选文件立即投递上传"
        @click="emit('open-manual')"
      >
        🚀 手动投递任务
      </button>
      <div class="dock-bottom-sub-row">
        <button type="button" class="dock-bottom-btn add-path-btn" @click="promptAddWatchPath">
          + 添加监控路径
        </button>
        <button
          type="button"
          class="dock-bottom-btn global-toggle-btn"
          :class="{ 'is-running': watchEnabled, 'is-paused': !watchEnabled }"
          :disabled="togglingGlobalWatch"
          :title="watchEnabled ? '点击一键暂停全局自动监控' : '点击一键开启全局自动监控并全量补扫'"
          @click="handleToggleGlobalWatch"
        >
          <span class="global-watch-dot" :class="{ on: watchEnabled }"></span>
          <span>{{ watchEnabled ? "全局监控中" : "监控已暂停" }}</span>
        </button>
      </div>
    </div>
  </aside>
</template>

<style scoped src="./WatchDock.css"></style>

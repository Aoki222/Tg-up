<script setup lang="ts">
import { formatBytes } from "../format";
import { t } from "../i18n";
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
        <h2>{{ t("dock.heading") }}</h2>
        <span class="dock-count-badge" :class="{ 'is-paused': !watchEnabled }">
          {{ !watchEnabled ? t("dock.paused") : t("dock.active", { n: activeRootsCount }) }}
        </span>
      </div>

      <div class="dock-head-right">
        <button
          v-if="expandedPaths.size > 0"
          type="button"
          class="dock-collapse-all-btn font-mono"
          :title="t('dock.collapseAllTitle')"
          @click="expandedPaths.clear()"
        >
          {{ t("dock.collapseAll") }}
        </button>
        <button type="button" class="dock-toggle-btn" :title="t('dock.collapseTitle')" @click="emit('close')">
          {{ t("dock.collapse") }}
        </button>
      </div>
    </div>

    <div class="dock-list">
      <p v-if="dockItems.length === 0" class="dock-empty">
        {{ t("dock.empty") }}
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
          :title="isCardExpanded(item.path) ? t('dock.collapseCard') : t('dock.expandCard')"
          @click="toggleCardExpand(item.path)"
        >
          <button
            type="button"
            class="dock-fold-arrow"
            :class="{ expanded: isCardExpanded(item.path) }"
            :title="isCardExpanded(item.path) ? t('dock.collapseCard') : t('dock.expandCard')"
            @click.stop="toggleCardExpand(item.path)"
          >
            {{ isCardExpanded(item.path) ? "▼" : "▶" }}
          </button>
          <span class="dock-dot" :class="{ bad: !item.ok }" :title="item.error || t('dock.readable')"></span>
          <strong :title="item.path" class="dock-title-name">{{ item.name }}</strong>

          <div class="dock-actions" @click.stop>
            <el-switch
              :model-value="item.enabled"
              size="small"
              class="dock-root-switch"
              :disabled="togglingRoots.has(normPath(item.path))"
              :title="item.enabled ? t('dock.pauseRoot') : t('dock.resumeRoot')"
              @change="handleToggleRoot(item.path)"
            />
            <button
              type="button"
              class="dock-policy-btn"
              :title="t('dock.policyTitle')"
              @click="emit('open-policy', item.path)"
            >
              {{ t("dock.policy") }}
            </button>
            <button
              type="button"
              class="dock-delete-btn"
              :title="t('dock.deleteTitle')"
              @click="removeWatchPath(item.path)"
            >
              {{ t("dock.delete") }}
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
            {{ child.label }}{{ !child.enabled ? t("dock.blockedSuffix") : "" }}
          </button>
        </div>

        <div v-if="isCardExpanded(item.path)" class="card-inline-tree-container" @click.stop>
          <div class="inline-tree-toolbar">
            <span class="inline-tree-hint font-mono">{{ t("dock.passthrough") }}</span>
            <button
              type="button"
              class="inline-refresh-btn font-mono"
              :title="t('dock.refreshTitle')"
              @click="refreshCardRoot(item.path)"
            >
              {{ t("dock.refresh") }}
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
                    :title="t('dock.blockedTitle')"
                  >
                    {{ t("dock.blocked") }}
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
                    {{ data.current_route?.is_inherited ? t('dock.inherited') : t('dock.own') }}: {{ data.current_route?.dest_name }}
                  </span>
                  <template v-if="!data.is_dir">
                    <span class="file-size font-mono">{{ formatBytes(data.size || 0) }}</span>
                    <span v-if="!data.supported_ext" class="file-status status-gray">{{ t("dock.unwatched") }}</span>
                    <span v-else-if="data.task_status === 'uploading'" class="file-status status-uploading">{{ t("dock.uploading") }}</span>
                    <span v-else-if="data.task_status === 'success'" class="file-status status-success">{{ t("dock.uploaded") }}</span>
                    <span v-else-if="data.task_status === 'pending' || data.task_status === 'preparing'" class="file-status status-pending">{{ t("dock.queued") }}</span>
                    <span v-else-if="data.task_status === 'failed'" class="file-status status-failed" :title="data.task_error || t('dock.failed')">{{ t("dock.failed") }}</span>
                    <span v-else-if="data.task_status === 'unmatched'" class="file-status status-unmatched">{{ t("dock.unmatched") }}</span>
                  </template>
                </div>

                <div v-if="data.is_dir" class="node-right shrink-0" @click.stop>
                  <el-switch
                    :model-value="isSubfolderEnabled(data)"
                    size="small"
                    class="tree-dir-switch"
                    :disabled="togglingRoutes.has(normPath(data.path))"
                    :title="isSubfolderEnabled(data) ? t('dock.blockChild') : t('dock.unblockChild')"
                    @change="handleToggleSubfolder(data.path, node, data)"
                  />

                  <button
                    type="button"
                    class="tree-policy-btn"
                    :title="t('dock.childPolicy')"
                    @click="emit('open-policy', data.path)"
                  >
                    {{ t("dock.policy") }}
                  </button>
                  <button
                    v-if="data.current_route?.matched && !data.current_route?.is_inherited && !data.current_route?.auto_blocked && isSubfolderEnabled(data)"
                    type="button"
                    class="tree-inherit-btn"
                    :title="t('dock.restoreTitle')"
                    @click="restoreSubfolderInheritance(data.path)"
                  >
                    {{ t("dock.restore") }}
                  </button>
                  <button
                    type="button"
                    class="tree-refresh-icon-btn"
                    :title="t('dock.refreshChild')"
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
        :title="t('dock.manualTitle')"
        @click="emit('open-manual')"
      >
        {{ t("dock.manual") }}
      </button>
      <div class="dock-bottom-sub-row">
        <button type="button" class="dock-bottom-btn add-path-btn" @click="promptAddWatchPath">
          {{ t("dock.add") }}
        </button>
        <button
          type="button"
          class="dock-bottom-btn global-toggle-btn"
          :class="{ 'is-running': watchEnabled, 'is-paused': !watchEnabled }"
          :disabled="togglingGlobalWatch"
          :title="watchEnabled ? t('dock.globalOnTitle') : t('dock.globalOffTitle')"
          @click="handleToggleGlobalWatch"
        >
          <span class="global-watch-dot" :class="{ on: watchEnabled }"></span>
          <span>{{ watchEnabled ? t("dock.globalOn") : t("dock.globalOff") }}</span>
        </button>
      </div>
    </div>
  </aside>
</template>

<style scoped src="./WatchDock.css"></style>

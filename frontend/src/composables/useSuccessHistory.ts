/**
 * 传输历史弹层：今天 / 累计口径、分页和本地过滤。
 */
import { computed, onMounted, ref, watch, type Ref } from "vue";
import { fetchSuccessPage, type SuccessRecord } from "../api";

const SUCCESS_SCOPE_KEY = "uploader.success-scope";

export function useSuccessHistory(successToday: Ref<number>) {
  const successScope = ref<"today" | "all">(loadSuccessScope());
  const successCount = computed(() => successToday.value);
  const successAllTotal = ref(0);
  const successOpen = ref(false);
  const successPage = ref(1);
  const successPageSize = 50;
  const successTotalRows = ref(0);
  const successItems = ref<SuccessRecord[]>([]);
  const successLoading = ref(false);
  const successSearchText = ref("");

  const successPages = computed(() =>
    Math.max(1, Math.ceil(successTotalRows.value / successPageSize)),
  );

  const filteredSuccessItems = computed(() => {
    const query = successSearchText.value.trim().toLowerCase();
    if (!query) return successItems.value;
    return successItems.value.filter(
      (item) =>
        item.file_name.toLowerCase().includes(query) ||
        (item.folder_name && item.folder_name.toLowerCase().includes(query)),
    );
  });

  function loadSuccessScope(): "today" | "all" {
    return localStorage.getItem(SUCCESS_SCOPE_KEY) === "all" ? "all" : "today";
  }

  async function refreshSuccessTotals(): Promise<void> {
    try {
      const dataAll = await fetchSuccessPage("all", 1, 1);
      successAllTotal.value = dataAll.total;
    } catch {
      // 忽略异常
    }
  }

  function onSuccessScopeChange(value: "today" | "all"): void {
    successScope.value = value;
    localStorage.setItem(SUCCESS_SCOPE_KEY, value);
    if (successOpen.value) {
      successPage.value = 1;
      void loadSuccess();
    }
  }

  function toggleSuccessScope(event?: Event): void {
    if (event) event.stopPropagation();
    const next = successScope.value === "today" ? "all" : "today";
    onSuccessScopeChange(next);
  }

  async function loadSuccess(): Promise<void> {
    successLoading.value = true;
    try {
      const data = await fetchSuccessPage(successScope.value, successPage.value, successPageSize);
      successItems.value = data.items;
      successTotalRows.value = data.total;
      successPage.value = data.page;
      if (successScope.value === "all") {
        successAllTotal.value = data.total;
      }
    } catch {
      successItems.value = [];
      successTotalRows.value = 0;
    } finally {
      successLoading.value = false;
    }
  }

  function openSuccess(): void {
    successOpen.value = true;
    successPage.value = 1;
    void loadSuccess();
  }

  function closeSuccess(): void {
    successOpen.value = false;
    successSearchText.value = "";
  }

  async function shiftSuccessPage(step: number): Promise<void> {
    const next = successPage.value + step;
    if (next < 1 || next > successPages.value) return;
    successPage.value = next;
    await loadSuccess();
  }

  onMounted(() => {
    void refreshSuccessTotals();
  });

  watch(successCount, () => {
    void refreshSuccessTotals();
    if (successOpen.value && successPage.value === 1) void loadSuccess();
  });

  return {
    successScope,
    successCount,
    successAllTotal,
    successOpen,
    successPage,
    successPages,
    successTotalRows,
    successItems,
    successLoading,
    successSearchText,
    filteredSuccessItems,
    onSuccessScopeChange,
    toggleSuccessScope,
    loadSuccess,
    openSuccess,
    closeSuccess,
    shiftSuccessPage,
  };
}

/**
 * 传输历史弹层：今天 / 累计口径、分页和本地过滤。
 * 翻页时用 keepPreviousData 留着上一页，避免列表先被清空成「正在读取」。
 */
import { computed, onMounted, ref, watch, type Ref } from "vue";
import { keepPreviousData, useQuery } from "@tanstack/vue-query";
import { fetchSuccessPage } from "../api";

const SUCCESS_SCOPE_KEY = "uploader.success-scope";

export function useSuccessHistory(successToday: Ref<number>) {
  const successScope = ref<"today" | "all">(loadSuccessScope());
  const successCount = computed(() => successToday.value);
  const successAllTotal = ref(0);
  const successOpen = ref(false);
  const successPage = ref(1);
  const successPageSize = 50;
  const successSearchText = ref("");

  const historyQuery = useQuery({
    queryKey: computed(() => ["success-history", successScope.value, successPage.value] as const),
    queryFn: () => fetchSuccessPage(successScope.value, successPage.value, successPageSize),
    placeholderData: keepPreviousData,
    enabled: computed(() => successOpen.value),
    staleTime: 0,
  });

  const successItems = computed(() => historyQuery.data.value?.items ?? []);
  const successTotalRows = computed(() => historyQuery.data.value?.total ?? 0);
  const successLoading = computed(() => historyQuery.isFetching.value);
  const successPlaceholder = computed(() => historyQuery.isPlaceholderData.value);
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
    }
  }

  function toggleSuccessScope(event?: Event): void {
    if (event) event.stopPropagation();
    const next = successScope.value === "today" ? "all" : "today";
    onSuccessScopeChange(next);
  }

  function loadSuccess(): Promise<unknown> {
    return historyQuery.refetch();
  }

  function openSuccess(): void {
    successOpen.value = true;
    successPage.value = 1;
  }

  function closeSuccess(): void {
    successOpen.value = false;
    successSearchText.value = "";
  }

  async function shiftSuccessPage(step: number): Promise<void> {
    const next = successPage.value + step;
    if (next < 1 || next > successPages.value) return;
    successPage.value = next;
  }

  onMounted(() => {
    void refreshSuccessTotals();
  });

  watch(historyQuery.data, (data) => {
    if (!data || historyQuery.isPlaceholderData.value) return;
    if (data.page !== successPage.value) successPage.value = data.page;
    if (successScope.value === "all") successAllTotal.value = data.total;
  });

  watch(successCount, () => {
    void refreshSuccessTotals();
    if (successOpen.value && successPage.value === 1) void historyQuery.refetch();
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
    successPlaceholder,
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

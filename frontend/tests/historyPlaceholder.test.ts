import { afterEach, expect, test, vi } from "vitest";
import { createApp, defineComponent, h, ref } from "vue";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { useSuccessHistory } from "../src/composables/useSuccessHistory";
import { fetchSuccessPage, type SuccessPage, type SuccessRecord } from "../src/api";

vi.mock("../src/api", () => ({
  fetchSuccessPage: vi.fn(),
}));

function record(id: number, name: string): SuccessRecord {
  return {
    id,
    file_name: name,
    file_size: 10,
    folder_name: null,
    finished_at: null,
    part_label: null,
  };
}

function pageOf(page: number, name: string): SuccessPage {
  return { items: [record(page, name)], total: 100, page, page_size: 50 };
}

function mountHistory() {
  const successToday = ref(1);
  let api!: ReturnType<typeof useSuccessHistory>;
  const Root = defineComponent({
    setup() {
      api = useSuccessHistory(successToday);
      return () => h("div");
    },
  });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  const app = createApp(Root);
  app.use(VueQueryPlugin, { queryClient });
  const el = document.createElement("div");
  document.body.appendChild(el);
  app.mount(el);
  return {
    api,
    unmount() {
      app.unmount();
      el.remove();
      queryClient.clear();
    },
  };
}

afterEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

test("history page change keeps the previous rows until the next page arrives", async () => {
  const pending = new Map<string, (value: SuccessPage) => void>();
  vi.mocked(fetchSuccessPage).mockImplementation((scope, page, pageSize = 50) => {
    if (pageSize === 1) {
      return Promise.resolve({ items: [], total: 100, page: 1, page_size: 1 });
    }
    return new Promise((resolve) => {
      pending.set(`${scope}:${page}`, resolve);
    });
  });

  const { api, unmount } = mountHistory();
  api.openSuccess();
  await vi.waitFor(() => {
    expect(pending.has("today:1")).toBe(true);
  });
  pending.get("today:1")!(pageOf(1, "first.mp4"));
  await vi.waitFor(() => {
    expect(api.filteredSuccessItems.value.map((item) => item.file_name)).toEqual(["first.mp4"]);
  });

  await api.shiftSuccessPage(1);
  await vi.waitFor(() => {
    expect(pending.has("today:2")).toBe(true);
  });
  expect(api.successPage.value).toBe(2);
  expect(api.successPlaceholder.value).toBe(true);
  expect(api.filteredSuccessItems.value.map((item) => item.file_name)).toEqual(["first.mp4"]);

  pending.get("today:2")!(pageOf(2, "second.mp4"));
  await vi.waitFor(() => {
    expect(api.filteredSuccessItems.value.map((item) => item.file_name)).toEqual(["second.mp4"]);
  });
  expect(api.successPlaceholder.value).toBe(false);
  unmount();
});

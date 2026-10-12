import { afterEach, expect, test, vi } from "vitest";
import { apiClient, normalizeSettings, openProgressStream, setApiToken } from "../src/api";
import type { UploadConfig } from "../src/types";
import { pausedObserverRoot } from "../src/watchPath";

vi.mock("element-plus", () => ({
  ElMessage: { error: () => undefined, success: () => undefined },
}));

function config(over: Partial<UploadConfig> = {}): UploadConfig {
  return {
    chat_id: -100,
    observer_roots: [],
    page_dir: "page",
    archive_dir: "uploaded",
    preview: "off",
    caption_template: "",
    topic_creation_enabled: true,
    after_success: "keep",
    auto_slice: false,
    concurrency: 1,
    max_retries: 3,
    upload_timeout_seconds: 1200,
    assigned_timeout_seconds: 600,
    stable_timeout_seconds: 1800,
    watch_extensions: [],
    routes: [],
    chats: [],
    drive_folders: [],
    revision: 42,
    schedule_order: "oldest",
    ...over,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  setApiToken("");
});

test("normalizeSettings keeps revision and only smallest or oldest", () => {
  const kept = normalizeSettings(config());
  expect(kept.revision).toBe(42);
  expect(kept.schedule_order).toBe("oldest");

  const raw = config();
  (raw as { schedule_order: string }).schedule_order = "newest";
  expect(normalizeSettings(raw).schedule_order).toBe("oldest");
  expect(normalizeSettings(config({ revision: undefined, schedule_order: undefined })).schedule_order).toBe(
    "oldest",
  );
  expect(normalizeSettings(config({ schedule_order: "smallest" })).schedule_order).toBe("smallest");
});

test("a new watch path is added paused", () => {
  const root = pausedObserverRoot("D:/incoming");
  expect(root.enabled).toBe(false);
  expect(root.path).toBe("D:/incoming");
});

test("a token opens the progress stream with a short ticket", async () => {
  setApiToken("long-lived-api-token-value");
  const post = vi.spyOn(apiClient, "post").mockResolvedValue({
    data: { ticket: "short-ticket" },
    status: 200,
    statusText: "OK",
    headers: {},
    config: { headers: {} },
  } as never);
  const urls: string[] = [];
  vi.stubGlobal(
    "EventSource",
    class {
      constructor(url: string) {
        urls.push(url);
      }
      addEventListener(): void {}
      onerror: (() => void) | null = null;
    },
  );

  await openProgressStream(() => undefined);

  expect(post).toHaveBeenCalledWith("/api/progress/ticket");
  expect(urls).toEqual(["/api/progress/stream?ticket=short-ticket"]);
  expect(urls[0]).not.toContain("long-lived-api-token-value");
});

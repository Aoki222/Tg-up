import type { Page, Route } from "@playwright/test";

export type ScheduleOrder = "oldest" | "natural" | "created" | "smallest";
export type BoardStatus =
  | "preparing"
  | "pending"
  | "assigned"
  | "uploading"
  | "oversized"
  | "success"
  | "failed";

export interface BoardTask {
  id: number;
  file_name: string;
  file_size: number;
  folder_name: string | null;
  status: BoardStatus;
  assigned_worker: string | null;
  platform: string;
  dest_id: string;
  retry_count: number;
  max_retries: number;
  error: string | null;
  created_at: string | null;
  started_at: string | null;
  percent: number;
  current: number;
  total: number;
  speed_bps: number;
  eta_seconds: number;
  stage: "uploading" | "success" | "failed" | "flood_wait" | null;
  message: string;
  sliceable: boolean;
  slice_phase: "idle" | "queued" | "cutting" | "released" | "uploading" | "failed" | "blocked";
  slice_min_parts: number | null;
  part_index: number | null;
  part_count: number | null;
}

export interface ProgressEvent {
  task_id: number;
  worker_name: string;
  file_name: string;
  current: number;
  total: number;
  percent: number;
  stage: "uploading" | "success" | "failed" | "flood_wait";
  speed_bps: number;
  eta_seconds: number;
  message: string;
  part_index?: number | null;
  part_count?: number | null;
}

export interface ObserverRoot {
  path: string;
  enabled: boolean;
  ok: boolean;
  error: string;
}

export interface Settings {
  revision: number;
  schedule_order: ScheduleOrder;
  watch_enabled: boolean;
  chat_id: number;
  observer_roots: ObserverRoot[];
  observer_paths: string[];
  observer_path_infos: { path: string; ok: boolean; error: string }[];
  page_dir: string;
  archive_dir: string;
  preview: string;
  grid_layout: string;
  grid_timestamp: boolean;
  grid_metadata: boolean;
  caption_template: string;
  topic_creation_enabled: boolean;
  after_success: string;
  auto_slice: boolean;
  concurrency: number;
  max_retries: number;
  upload_timeout_seconds: number;
  assigned_timeout_seconds: number;
  stable_timeout_seconds: number;
  watch_extensions: string[];
  routes: unknown[];
  chats: { chat_id: number; alias: string; title: string }[];
  drive_folders: unknown[];
}

export interface Worker {
  name: string;
  username: string | null;
  enabled: boolean;
  running: boolean;
  accepting: boolean;
  flood_wait_seconds: number;
  queue_size: number;
  in_flight: number;
  reconnecting: boolean;
  reconnect_attempt: number;
  reconnect_max: number;
  reconnect_wait_seconds: number;
  reconnect_error: string;
}

export interface SuccessRecord {
  id: number;
  file_name: string;
  file_size: number;
  folder_name: string | null;
  finished_at: string | null;
  part_label: string | null;
}

export interface Identity {
  api_id: number;
  api_hash_masked: string;
  configured: boolean;
}

export function task(partial: Pick<BoardTask, "id" | "file_name" | "status"> & Partial<BoardTask>): BoardTask {
  return {
    file_size: 1024,
    folder_name: "inbox",
    assigned_worker: null,
    platform: "telegram",
    dest_id: "-1001",
    retry_count: 0,
    max_retries: 3,
    error: null,
    created_at: "2026-10-12T00:00:00",
    started_at: null,
    percent: 0,
    current: 0,
    total: 0,
    speed_bps: 0,
    eta_seconds: -1,
    stage: null,
    message: "",
    sliceable: false,
    slice_phase: "idle",
    slice_min_parts: null,
    part_index: null,
    part_count: null,
    ...partial,
  };
}

export function worker(partial: Pick<Worker, "name"> & Partial<Worker>): Worker {
  return {
    username: null,
    enabled: true,
    running: true,
    accepting: true,
    flood_wait_seconds: 0,
    queue_size: 0,
    in_flight: 0,
    reconnecting: false,
    reconnect_attempt: 0,
    reconnect_max: 5,
    reconnect_wait_seconds: 0,
    reconnect_error: "",
    ...partial,
  };
}

export function defaultSettings(): Settings {
  const root: ObserverRoot = {
    path: "D:/media/inbox",
    enabled: true,
    ok: true,
    error: "",
  };
  return {
    revision: 42,
    schedule_order: "oldest",
    watch_enabled: true,
    chat_id: -100123,
    observer_roots: [root],
    observer_paths: [root.path],
    observer_path_infos: [{ path: root.path, ok: true, error: "" }],
    page_dir: "page",
    archive_dir: "uploaded",
    preview: "first_frame",
    grid_layout: "3x5",
    grid_timestamp: true,
    grid_metadata: true,
    caption_template: "",
    topic_creation_enabled: true,
    after_success: "keep",
    auto_slice: false,
    concurrency: 1,
    max_retries: 3,
    upload_timeout_seconds: 1200,
    assigned_timeout_seconds: 600,
    stable_timeout_seconds: 1800,
    watch_extensions: ["mp4", "mkv", "avi", "mov"],
    routes: [],
    chats: [],
    drive_folders: [],
  };
}

export class World {
  settings = defaultSettings();
  settingsPuts: Settings[] = [];
  settingsStatus: 200 | 409 = 200;
  identity: Identity = { api_id: 123456, api_hash_masked: "abc***", configured: true };
  identityPuts: { api_id: number; api_hash: string }[] = [];
  workers: Worker[] = [worker({ name: "worker_1" })];
  unmatched: { id: number; file_path: string; file_name: string; folder_name: string | null; file_size: number; discovered_at: string | null }[] = [];
  board: BoardTask[] = [];
  failed: BoardTask[] = [];
  oversized: BoardTask[] = [];
  successToday = 0;
  successTodayItems: SuccessRecord[] = [];
  successAllItems: SuccessRecord[] = [];
  boardReads = 0;
  streamOpens = 0;
  version = {
    current_version: "2.4.1",
    remote_version: null as string | null,
    has_update: false,
    channel: "staging" as const,
    status: "staging" as const,
    commit_message: "测试提交",
    commit_url: "",
  };
  sessions = {
    items: ["worker_1"],
    accounts: [{ name: "worker_1", kind: "user", username: "" }],
    default_group_id: null as number | null,
    api_configured: true,
  };
  slices: { id: number; parts: number }[] = [];
  dispatches: number[] = [];
  /**
   * 下一条 SSE 连接要写下的帧。路由处理不能挂起，否则同页后面的接口会排不上。
   * 没有帧时回一个 200ms 后重连的注释，等 push 把真正的 progress 放进来。
   */
  pendingFrames: string[] = [];

  async pushProgress(event: ProgressEvent): Promise<void> {
    const frame = `retry: 200\nevent: progress\ndata: ${JSON.stringify(event)}\n\n`;
    this.pendingFrames.push(frame);
    const started = Date.now();
    while (this.pendingFrames.includes(frame)) {
      if (this.streamOpens === 0 && Date.now() - started > 8_000) {
        throw new Error("页面没有连上 /api/progress/stream");
      }
      if (Date.now() - started > 8_000) {
        throw new Error("进度事件没有被 SSE 连接取走");
      }
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
  }

  settle(): void {
    this.pendingFrames = [];
  }
}

export function createWorld(): World {
  return new World();
}

function json(route: Route, body: unknown, status = 200): Promise<void> {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

function readJson(route: Route): unknown {
  const raw = route.request().postData();
  if (!raw) return {};
  return JSON.parse(raw) as unknown;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function pageOf<T>(items: T[], page: number, pageSize: number): T[] {
  const start = Math.max(0, (page - 1) * pageSize);
  return items.slice(start, start + pageSize);
}

function moveToPending(world: World, id: number): void {
  const source = world.failed.find((item) => item.id === id) ?? world.board.find((item) => item.id === id);
  world.failed = world.failed.filter((item) => item.id !== id);
  world.board = world.board.filter((item) => item.id !== id);
  if (!source) return;
  world.board.push({
    ...source,
    status: "pending",
    stage: null,
    error: null,
    percent: 0,
    current: 0,
    total: 0,
    speed_bps: 0,
    eta_seconds: -1,
    message: "",
  });
}

function forgetTask(world: World, id: number): void {
  world.board = world.board.filter((item) => item.id !== id);
  world.failed = world.failed.filter((item) => item.id !== id);
  world.oversized = world.oversized.filter((item) => item.id !== id);
}

async function dispatch(route: Route, world: World): Promise<void> {
  const request = route.request();
  const url = new URL(request.url());
  const path = decodeURIComponent(url.pathname);
  const method = request.method();

  if (path === "/api/progress/stream" && method === "GET") {
    world.streamOpens += 1;
    const frame = world.pendingFrames.shift() ?? "retry: 200\n: keep\n\n";
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream; charset=utf-8",
      headers: { "cache-control": "no-cache" },
      body: frame,
    });
    return;
  }
  if (path === "/api/progress/ticket" && method === "POST") {
    await json(route, { ticket: "test-ticket" });
    return;
  }
  if (path === "/api/system/version" && method === "GET") {
    await json(route, world.version);
    return;
  }
  if (path === "/api/workers" && method === "GET") {
    await json(route, { items: world.workers });
    return;
  }
  const workerAction = path.match(/^\/api\/workers\/([^/]+)\/(disable|enable)$/);
  if (workerAction && method === "POST") {
    const name = workerAction[1];
    const enable = workerAction[2] === "enable";
    world.workers = world.workers.map((item) =>
      item.name === name
        ? { ...item, enabled: enable, running: enable ? item.running || true : item.running, accepting: enable }
        : item,
    );
    await json(route, { ok: true });
    return;
  }
  if (path === "/api/identity" && method === "GET") {
    await json(route, world.identity);
    return;
  }
  if (path === "/api/identity" && method === "PUT") {
    const body = asRecord(readJson(route));
    const apiId = Number(body.api_id);
    const apiHash = String(body.api_hash ?? "");
    world.identityPuts.push({ api_id: apiId, api_hash: apiHash });
    world.identity = { api_id: apiId, api_hash_masked: "new***", configured: true };
    await json(route, { ok: true });
    return;
  }
  if (path === "/api/unmatched" && method === "GET") {
    await json(route, { items: world.unmatched, count: world.unmatched.length });
    return;
  }
  if (path === "/api/settings" && method === "GET") {
    await json(route, world.settings);
    return;
  }
  if (path === "/api/settings" && method === "PUT") {
    const body = readJson(route) as Settings;
    world.settingsPuts.push(body);
    if (world.settingsStatus === 409) {
      await json(route, { detail: "配置已在别处修改，请重新加载后再保存" }, 409);
      return;
    }
    world.settings = { ...body, revision: Number(body.revision ?? world.settings.revision) + 1 };
    await json(route, world.settings);
    return;
  }
  if (path === "/api/settings/watch/toggle" && method === "POST") {
    const next = !world.settings.watch_enabled;
    world.settings = {
      ...world.settings,
      watch_enabled: next,
      observer_roots: world.settings.observer_roots.map((item) => ({ ...item, enabled: next })),
    };
    await json(route, {
      watch_enabled: world.settings.watch_enabled,
      observer_roots: world.settings.observer_roots,
    });
    return;
  }
  if (path === "/api/settings/roots/toggle" && method === "POST") {
    const body = asRecord(readJson(route));
    const target = String(body.path ?? "");
    world.settings = {
      ...world.settings,
      observer_roots: world.settings.observer_roots.map((item) =>
        item.path === target ? { ...item, enabled: !item.enabled } : item,
      ),
    };
    world.settings.watch_enabled = world.settings.observer_roots.some((item) => item.enabled);
    await json(route, {
      path: target,
      observer_roots: world.settings.observer_roots,
      watch_enabled: world.settings.watch_enabled,
    });
    return;
  }
  if (path === "/api/tasks/success" && method === "GET") {
    const scope = url.searchParams.get("scope") === "today" ? "today" : "all";
    const page = Number(url.searchParams.get("page") || "1");
    const pageSize = Number(url.searchParams.get("page_size") || "50");
    const items = scope === "today" ? world.successTodayItems : world.successAllItems;
    await json(route, {
      items: pageOf(items, page, pageSize),
      total: items.length,
      page,
      page_size: pageSize,
    });
    return;
  }
  if (path === "/api/tasks/failed" && method === "GET") {
    const page = Number(url.searchParams.get("page") || "1");
    const pageSize = Number(url.searchParams.get("page_size") || "50");
    await json(route, {
      items: pageOf(world.failed, page, pageSize),
      total: world.failed.length,
      page,
      page_size: pageSize,
    });
    return;
  }
  if (path === "/api/tasks/failed/delete" && method === "POST") {
    const body = asRecord(readJson(route));
    const ids = Array.isArray(body.ids) ? body.ids.map((id) => Number(id)) : [];
    const before = world.failed.length;
    world.failed = world.failed.filter((item) => !ids.includes(item.id));
    await json(route, { ok: true, deleted: before - world.failed.length });
    return;
  }
  if (path === "/api/tasks/failed" && method === "DELETE") {
    const deleted = world.failed.length;
    world.failed = [];
    await json(route, { ok: true, deleted });
    return;
  }
  if (path === "/api/tasks/retry-failed" && method === "POST") {
    const retried = world.failed.length;
    for (const item of [...world.failed]) moveToPending(world, item.id);
    await json(route, { ok: true, retried, skipped: 0, parked: 0 });
    return;
  }
  if (path === "/api/tasks/oversized" && method === "GET") {
    const page = Number(url.searchParams.get("page") || "1");
    const pageSize = Number(url.searchParams.get("page_size") || "50");
    await json(route, {
      items: pageOf(world.oversized, page, pageSize),
      total: world.oversized.length,
      page,
      page_size: pageSize,
    });
    return;
  }
  if (path === "/api/tasks" && method === "GET") {
    world.boardReads += 1;
    const count = (status: BoardStatus) => world.board.filter((item) => item.status === status).length;
    await json(route, {
      items: world.board,
      counts: {
        preparing: count("preparing"),
        pending: count("pending"),
        assigned: count("assigned"),
        uploading: count("uploading"),
        oversized: world.oversized.length,
        failed: world.failed.length,
        success: world.successAllItems.length,
        success_today: world.successToday,
      },
    });
    return;
  }
  const retry = path.match(/^\/api\/tasks\/(\d+)\/retry$/);
  if (retry && method === "POST") {
    moveToPending(world, Number(retry[1]));
    await json(route, { status: "pending" });
    return;
  }
  const slice = path.match(/^\/api\/tasks\/(\d+)\/slice$/);
  if (slice && method === "POST") {
    const body = asRecord(readJson(route));
    world.slices.push({ id: Number(slice[1]), parts: Number(body.parts) });
    await json(route, { ok: true });
    return;
  }
  const dispatchUser = path.match(/^\/api\/tasks\/(\d+)\/dispatch-user$/);
  if (dispatchUser && method === "POST") {
    world.dispatches.push(Number(dispatchUser[1]));
    await json(route, { ok: true });
    return;
  }
  const cont = path.match(/^\/api\/tasks\/(\d+)\/slice-continue$/);
  if (cont && method === "POST") {
    await json(route, { ok: true });
    return;
  }
  const remove = path.match(/^\/api\/tasks\/(\d+)$/);
  if (remove && method === "DELETE") {
    forgetTask(world, Number(remove[1]));
    await json(route, { ok: true });
    return;
  }
  if (path === "/api/sessions" && method === "GET") {
    await json(route, world.sessions);
    return;
  }
  if (path === "/api/fs/nodes" && method === "GET") {
    await json(route, { items: [], path: url.searchParams.get("path") ?? "" });
    return;
  }
  if (path === "/api/chats" && method === "GET") {
    await json(route, { items: [], online: true, reason: "" });
    return;
  }
  await json(route, { items: [], total: 0, count: 0, ok: true });
}

export async function installApi(page: Page, world: World): Promise<void> {
  await page.route("**/api/**", async (route) => {
    try {
      await dispatch(route, world);
    } catch {
      await route.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"mock failed"}' }).catch(() => undefined);
    }
  });
}

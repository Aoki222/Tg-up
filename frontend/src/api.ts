import type {
  ObserverPathInfo,
  SessionLoginResult,
  SessionMeta,
  SessionMode,
  UploadConfig,
  UploadProgress,
  WorkerSnapshot,
} from "./types";

async function readJson<T>(response: Response, label: string): Promise<T> {
  if (!response.ok) {
    let detail = `${label} ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) {
        detail = body.detail;
      }
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export async function fetchWorkers(): Promise<WorkerSnapshot[]> {
  const response = await fetch("/api/workers");
  const data = await readJson<{ items: WorkerSnapshot[] }>(response, "workers");
  return data.items;
}

export async function disableWorker(name: string): Promise<void> {
  const response = await fetch(`/api/workers/${encodeURIComponent(name)}/disable`, { method: "POST" });
  await readJson(response, "disable-worker");
}

export async function enableWorker(name: string): Promise<void> {
  const response = await fetch(`/api/workers/${encodeURIComponent(name)}/enable`, { method: "POST" });
  await readJson(response, "enable-worker");
}

export async function deleteWorker(name: string): Promise<void> {
  const response = await fetch(`/api/workers/${encodeURIComponent(name)}`, { method: "DELETE" });
  await readJson(response, "delete-worker");
}

function normalizeSettings(data: UploadConfig): UploadConfig {
  const infos: ObserverPathInfo[] = data.observer_path_infos ?? [];
  const paths = infos.length ? infos.map((item) => item.path) : data.observer_paths;
  return { ...data, observer_paths: paths, observer_path_infos: infos };
}

export async function fetchSettings(): Promise<UploadConfig> {
  const response = await fetch("/api/settings");
  return normalizeSettings(await readJson<UploadConfig>(response, "settings"));
}

export async function saveSettings(payload: UploadConfig): Promise<UploadConfig> {
  const response = await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...payload,
      observer_paths: payload.observer_paths,
    }),
  });
  return normalizeSettings(await readJson<UploadConfig>(response, "settings"));
}

export async function fetchSessionMeta(): Promise<SessionMeta> {
  const response = await fetch("/api/sessions");
  return readJson<SessionMeta>(response, "sessions");
}

export async function startSessionLogin(payload: {
  mode: SessionMode;
  bot_token: string;
  phone: string;
  bind_group: boolean;
  group_id: number | null;
  force: boolean;
}): Promise<SessionLoginResult> {
  const response = await fetch("/api/sessions/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return readJson<SessionLoginResult>(response, "sessions");
}

export async function submitSessionCode(login_id: string, code: string): Promise<SessionLoginResult> {
  const response = await fetch("/api/sessions/code", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ login_id, code }),
  });
  return readJson<SessionLoginResult>(response, "sessions");
}

export async function submitSessionPassword(
  login_id: string,
  password: string,
): Promise<SessionLoginResult> {
  const response = await fetch("/api/sessions/password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ login_id, password }),
  });
  return readJson<SessionLoginResult>(response, "sessions");
}

export function openProgressStream(
  onEvent: (progress: UploadProgress) => void,
  onError?: () => void,
): EventSource {
  const source = new EventSource("/api/progress/stream");
  source.addEventListener("progress", (event: Event) => {
    const message = event as MessageEvent<string>;
    const progress = JSON.parse(message.data) as UploadProgress;
    onEvent(progress);
  });
  source.onerror = () => {
    onError?.();
  };
  return source;
}

export type ProgressStage = "uploading" | "success" | "failed" | "flood_wait";

export interface UploadProgress {
  task_id: number;
  worker_name: string;
  file_name: string;
  current: number;
  total: number;
  percent: number;
  stage: ProgressStage;
  message: string;
}

export interface WorkerSnapshot {
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

export type PreviewMode = "off" | "first_frame" | "grid";
export type AfterSuccess = "keep" | "delete" | "move_to_archive";

export type SessionMode = "bot" | "user";

export interface SessionLoginResult {
  done: boolean;
  step: "code" | "password" | "done";
  login_id: string | null;
  name: string | null;
  username: string | null;
  is_bot: boolean;
  group_ok: boolean | null;
  group_error: string | null;
  message: string;
}

export interface SessionMeta {
  items: string[];
  default_group_id: number | null;
  api_configured: boolean;
}

export interface ObserverPathInfo {
  path: string;
  ok: boolean;
  error: string;
}

export interface UploadConfig {
  chat_id: number;
  observer_paths: string[];
  observer_path_infos: ObserverPathInfo[];
  page_dir: string;
  archive_dir: string;
  preview: PreviewMode;
  topic_creation_enabled: boolean;
  after_success: AfterSuccess;
  concurrency: number;
  max_retries: number;
  upload_timeout_seconds: number;
  assigned_timeout_seconds: number;
  stable_timeout_seconds: number;
  watch_extensions: string[];
}

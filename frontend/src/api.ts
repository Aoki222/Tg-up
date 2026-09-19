/**
 * @file api.ts
 * @description 前端 HTTP API 通信与拦截器中枢
 *
 * 核心架构特性：
 * 1. 基于 Axios 统一封装网络客户端实例 `apiClient`，预置 15 秒请求超时；
 * 2. 请求拦截器：自动读取 localStorage 中的 API Token 并注入 `Authorization: Bearer <token>`；
 * 3. 响应拦截器：全局捕获 401 Unauthorized 状态码并弹出精准提示；自动解包 FastAPI 的 `detail` 错误文本；
 * 4. SSE 进度流：对于流式长连接 `/api/progress/stream`，遵循标准采用浏览器原生 `EventSource`。
 */

import axios, { type AxiosError } from "axios";
import { ElMessage } from "element-plus";
import type {
  BoardSnapshot,
  ObserverPathInfo,
  SessionLoginResult,
  SessionMeta,
  SessionMode,
  UploadConfig,
  UploadProgress,
  WorkerSnapshot,
} from "./types";

// ── Token 本地存储与持久化 ──────────────────────────────────────

const TOKEN_KEY = "uploader_api_token";

/**
 * 从浏览器 localStorage 中获取当前配置的 API 鉴权令牌
 * @returns 存储的 token 字符串，未配置时返回空串
 */
export function getApiToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? "";
}

/**
 * 更新或清空本地存储的 API Token
 * @param token 新的鉴权 Token，若为空串则从 localStorage 中清除
 */
export function setApiToken(token: string): void {
  if (token) {
    localStorage.setItem(TOKEN_KEY, token);
  } else {
    localStorage.removeItem(TOKEN_KEY);
  }
}

// ── Axios 客户端实例与全局拦截器 ─────────────────────────────────

export const apiClient = axios.create({
  timeout: 15000,
  headers: {
    "Content-Type": "application/json",
  },
});

/**
 * 【请求拦截器】
 * 在发起任何网络请求前，动态读取当前 Token 并追加至请求头，
 * 避免在各个业务函数中重复手写传递 headers。
 */
apiClient.interceptors.request.use((config) => {
  const token = getApiToken();
  if (token) {
    config.headers.set("Authorization", `Bearer ${token}`);
  }
  return config;
});

/**
 * 【响应拦截器】
 * 集中接管服务端异常响应：
 * 1. 遇到 401 鉴权失效时给出全局醒目 Toast 提示，引导用户前往「设置」页补充配置；
 * 2. 自动抽取后端 FastAPI 异常模型中的 `detail` 字段，解包为标准 Error 对象便于上层 catch。
 */
apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError<{ detail?: string }>) => {
    // 捕获鉴权异常 (401)
    if (error.response?.status === 401) {
      ElMessage.error("鉴权失败：API Token 无效或未提供，请在「设置」页配置");
    }

    // 统一提取服务端明确给出的 detail 错误提示
    let message = error.message || "网络请求失败";
    if (error.response?.data?.detail) {
      message = error.response.data.detail;
    }

    return Promise.reject(new Error(message));
  },
);

// ── Worker 节点控制相关 API ────────────────────────────────────

/**
 * 拉取当前所有已挂载的 Worker 状态快照列表
 */
export async function fetchWorkers(): Promise<WorkerSnapshot[]> {
  const res = await apiClient.get<{ items: WorkerSnapshot[] }>("/api/workers");
  return res.data.items;
}

/**
 * 临时禁用指定名称的 Worker（停止接收新分配的任务）
 * @param name Worker 实例标识名
 */
export async function disableWorker(name: string): Promise<void> {
  await apiClient.post(`/api/workers/${encodeURIComponent(name)}/disable`);
}

/**
 * 重新启用指定名称的 Worker（恢复抢单与队列分配）
 * @param name Worker 实例标识名
 */
export async function enableWorker(name: string): Promise<void> {
  await apiClient.post(`/api/workers/${encodeURIComponent(name)}/enable`);
}

/**
 * 物理删除指定名称的 Worker（删除对应 .session 文件并将未完成任务重置回待发队列）
 * @param name Worker 实例标识名
 */
export async function deleteWorker(name: string): Promise<void> {
  await apiClient.delete(`/api/workers/${encodeURIComponent(name)}`);
}

// ── 上传配置 (upload.toml) 相关 API ────────────────────────────

/**
 * 内部辅助函数：规范化配置中的监听目录结构，提取路径字符串数组
 */
function normalizeSettings(data: UploadConfig): UploadConfig {
  const infos: ObserverPathInfo[] = data.observer_path_infos ?? [];
  const paths = infos.length ? infos.map((item) => item.path) : data.observer_paths;
  return { ...data, observer_paths: paths, observer_path_infos: infos };
}

/**
 * 读取当前系统正在运行的生效配置参数
 */
export async function fetchSettings(): Promise<UploadConfig> {
  const res = await apiClient.get<UploadConfig>("/api/settings");
  return normalizeSettings(res.data);
}

/**
 * 将新的配置参数持久化写入 upload.toml，并在后端内存中实现热生效
 * @param payload 用户提交的表单配置
 */
export async function saveSettings(payload: UploadConfig): Promise<UploadConfig> {
  const res = await apiClient.put<UploadConfig>("/api/settings", {
    ...payload,
    observer_paths: payload.observer_paths,
  });
  return normalizeSettings(res.data);
}

// ── Session 授权登录流程 API ───────────────────────────────────

/**
 * 获取本地已有 Session 文件列表与默认群组配置
 */
export async function fetchSessionMeta(): Promise<SessionMeta> {
  const res = await apiClient.get<SessionMeta>("/api/sessions");
  return res.data;
}

/**
 * 第一步：发起新的 Session 登录握手（Bot Token 校验或向手机号下发验证码）
 */
export async function startSessionLogin(payload: {
  mode: SessionMode;
  bot_token: string;
  phone: string;
  bind_group: boolean;
  group_id: number | null;
  force: boolean;
}): Promise<SessionLoginResult> {
  const res = await apiClient.post<SessionLoginResult>("/api/sessions/start", payload);
  return res.data;
}

/**
 * 第二步：提交手机号收到的短信或 Telegram 官方服务通知验证码
 */
export async function submitSessionCode(login_id: string, code: string): Promise<SessionLoginResult> {
  const res = await apiClient.post<SessionLoginResult>("/api/sessions/code", { login_id, code });
  return res.data;
}

/**
 * 第三步（可选）：若账号开启了两步验证，向服务端提交 2FA 云端密码
 */
export async function submitSessionPassword(
  login_id: string,
  password: string,
): Promise<SessionLoginResult> {
  const res = await apiClient.post<SessionLoginResult>("/api/sessions/password", {
    login_id,
    password,
  });
  return res.data;
}

// ── 任务看板 ───────────────────────────────────────────────────

/**
 * 拉取看板任务快照（进行中全量 + 最近失败），上传中叠实时进度
 */
export async function fetchBoardTasks(): Promise<BoardSnapshot> {
  const res = await apiClient.get<BoardSnapshot>("/api/tasks");
  return res.data;
}

/**
 * 拉取当前仍在 uploading 的进度快照（含服务端速度）
 */
export async function fetchProgressSnapshot(): Promise<UploadProgress[]> {
  const res = await apiClient.get<{ items: UploadProgress[] }>("/api/progress");
  return res.data.items;
}

export async function retryBoardTask(taskId: number): Promise<void> {
  await apiClient.post(`/api/tasks/${taskId}/retry`);
}

export async function retryAllFailedTasks(): Promise<{ retried: number; skipped: number }> {
  const res = await apiClient.post<{ ok: boolean; retried: number; skipped: number }>(
    "/api/tasks/retry-failed",
  );
  return { retried: res.data.retried, skipped: res.data.skipped };
}

// ── SSE 实时进度长连接 ─────────────────────────────────────────

/**
 * 建立 Server-Sent Events (SSE) 进度推送长连接
 * @param onEvent 接收到单个任务进度更新时的回调函数
 * @param onError 网络闪断或长连接异常时的回调处理
 * @returns 原生 EventSource 实例（调用方负责在组件卸载时 close）
 */
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

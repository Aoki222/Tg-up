/**
 * @file types.ts
 * @description 前端核心 TypeScript 类型与接口定义
 * 包含上传进度数据模型、Worker 节点状态快照、热更配置项以及 Session 登录流程协议。
 */

// ── 上传进度相关类型 ───────────────────────────────────────────

/** 上传任务生命周期阶段：正在上传 | 上传成功 | 失败 | 触发限流等待 */
export type ProgressStage = "uploading" | "success" | "failed" | "flood_wait";

/** 后端 SSE 流式下发及快照中的原始上传进度事件对象 */
export interface UploadProgress {
  /** 任务唯一标识 ID */
  task_id: number;
  /** 执行该上传任务的 Worker 实例名（对应 sessions/<name>.session） */
  worker_name: string;
  /** 视频/文件原始文件名 */
  file_name: string;
  /** 当前已传输字节数（若是相册聚合任务，则为已传文件个数） */
  current: number;
  /** 文件总字节数（若是相册聚合任务，则为总文件个数） */
  total: number;
  /** 上传百分比（0.0 ~ 100.0） */
  percent: number;
  /** 当前上传阶段状态 */
  stage: ProgressStage;
  /** 附加消息（如异常错误原因、限流倒计时等） */
  message: string;
  /** 服务端 EMA 速度（字节/秒）。相册或未知为 0 */
  speed_bps: number;
  /** 剩余秒数。未知为 -1 */
  eta_seconds: number;
}

/** 后端任务生命周期。看板四列：preparing / pending / assigned+uploading / failed */
export type BoardStatus = "preparing" | "pending" | "assigned" | "uploading" | "success" | "failed";

/** GET /api/tasks 返回的看板行，上传中会叠 ProgressHub 的字节与速度 */
export interface BoardTask {
  id: number;
  file_name: string;
  file_size: number;
  folder_name: string | null;
  status: BoardStatus;
  assigned_worker: string | null;
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
  stage: ProgressStage | null;
  message: string;
}

export interface BoardCounts {
  preparing: number;
  pending: number;
  assigned: number;
  uploading: number;
  failed: number;
}

export interface BoardSnapshot {
  items: BoardTask[];
  counts: BoardCounts;
}

// ── Worker 节点相关类型 ─────────────────────────────────────────

/** Worker 运行时状态快照（由后端定周期轮询提供） */
export interface WorkerSnapshot {
  /** Session 标识名称（文件名无后缀） */
  name: string;
  /** Telegram 账号用户名（带 @，若未设置则为 null） */
  username: string | null;
  /** 管理员手动启用状态：true=启用, false=禁用 */
  enabled: boolean;
  /** 底层 Telethon 客户端连接并正在轮询：true=活跃运行中 */
  running: boolean;
  /** 是否允许接单：若处于限流或即将重启则为 false */
  accepting: boolean;
  /** 当前 FloodWait 限流倒计时剩余秒数（0 表示无限制） */
  flood_wait_seconds: number;
  /** 该 Worker 内部队列等待分配的任务数 */
  queue_size: number;
  /** 该 Worker 当前并发正在传输中的任务数 */
  in_flight: number;
  /** 是否正处于网络断线自动重连循环中 */
  reconnecting: boolean;
  /** 当前已尝试的重连次数 */
  reconnect_attempt: number;
  /** 最大允许重连上限 */
  reconnect_max: number;
  /** 下次重连退避等待秒数 */
  reconnect_wait_seconds: number;
  /** 最近一次连接异常报错详细信息 */
  reconnect_error: string;
}

// ── 上传配置 (upload.toml) 相关类型 ────────────────────────────

/** 视频封面生成模式：关闭 | 提取第一帧 | 生成时间轴网格缩略图 */
export type PreviewMode = "off" | "first_frame" | "grid";

/** 上传成功后本地原视频文件的处理策略：保留 | 直接删除 | 移动到归档目录 */
export type AfterSuccess = "keep" | "delete" | "move_to_archive";

/** 监听目录健康诊断信息 */
export interface ObserverPathInfo {
  /** 目录绝对或相对路径 */
  path: string;
  /** 目录是否存在且进程具备读取写入权限 */
  ok: boolean;
  /** 若不可用时的原因描述（如目录不存在） */
  error: string;
}

/** upload.toml 对应的完整配置负载 */
export interface UploadConfig {
  /** Telegram 目标频道或群组 chat_id（通常以 -100 开头） */
  chat_id: number;
  /** 监听目录路径数组（支持多目录并行监听） */
  observer_paths: string[];
  /** 后端诊断返回的监听目录状态详情 */
  observer_path_infos: ObserverPathInfo[];
  /** 封面缓存临时目录 */
  page_dir: string;
  /** 归档目录路径（当 after_success 为 move_to_archive 时生效） */
  archive_dir: string;
  /** 封面生成模式 */
  preview: PreviewMode;
  /** 是否自动在群组论坛（Forum）中按文件名/特征创建新 Topic */
  topic_creation_enabled: boolean;
  /** 上传成功后的本地文件处理动作 */
  after_success: AfterSuccess;
  /** 单个 Session 允许的最大并行上传流数量 */
  concurrency: number;
  /** 单个任务上传失败最大重试次数 */
  max_retries: number;
  /** 单文件上传最长超时时间（秒，防网络挂死） */
  upload_timeout_seconds: number;
  /** 任务被 Worker 抢占后最长未开工超时（秒） */
  assigned_timeout_seconds: number;
  /** 新增文件写稳等待时间（秒，防止文件写入未完成就提前开传） */
  stable_timeout_seconds: number;
  /** 监听的文件后缀扩展名（如 mp4, mkv 等，留空表示不限制） */
  watch_extensions: string[];
}

// ── Session 授权登录交互类型 ───────────────────────────────────

/** Session 登录模式：Telegram Bot 凭证登录 | 手机号短信/客户端验证码登录 */
export type SessionMode = "bot" | "user";

/** Session 登录握手与交互步进结果状态机 */
export interface SessionLoginResult {
  /** 当前登录全流程是否已成功完结 */
  done: boolean;
  /** 下一步需要客户端提交的内容：输入验证码(code) | 输入二步验证密码(password) | 已完成(done) */
  step: "code" | "password" | "done";
  /** 当前登录会话在服务端的暂存标识 ID */
  login_id: string | null;
  /** 成功登录后获取到的账号名/Session 标识名 */
  name: string | null;
  /** 成功登录后账号的 @username */
  username: string | null;
  /** 当前账号是否为 Bot 机器人账号 */
  is_bot: boolean;
  /** 若要求绑定群组，群组发送权限验证是否通过（null 表示未执行绑定） */
  group_ok: boolean | null;
  /** 群组校验失败时的详细原因（如：不是群管理员无法发帖） */
  group_error: string | null;
  /** 服务端返回的引导操作提示文本 */
  message: string;
}

/** 现有 Session 元数据信息（用于添加弹窗展示与默认群组预填） */
export interface SessionMeta {
  /** 当前服务器本地 sessions/ 目录下已持久化的 session 文件名列表 */
  items: string[];
  /** 默认绑定的目标群组 chat_id（来自配置） */
  default_group_id: number | null;
  /** 环境变量是否已正确配置 API_ID 与 API_HASH */
  api_configured: boolean;
}

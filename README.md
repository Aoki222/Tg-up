# uploader

监听目录里的视频，入库后用 Telegram session 上传到指定群（可选论坛话题 + 封面图）。

系统身份在 `.env`（改完要重启）。上传策略在 `upload.toml`（保存即热更新）。`sessions/*.session` 运行中放入/拿走会自动加载/卸载。

文件只从监听目录进来（默认 `download/`），没有 HTTP 投喂。

## 架构图

一个进程里同时跑流水线和 FastAPI 控制台。别的项目不要 import 本仓库。

### 分层

```mermaid
flowchart TB
  subgraph 入口
    MAIN["main.py"]
    UI["Vue 控制台 frontend/dist"]
  end

  subgraph pipeline["pipeline 主流程"]
    APP["application 装配 / 生命周期"]
    DIS["discover 监听 + 扫盘"]
    ING["ingest 写稳 / 入库 / 截图"]
    SCH["schedule 抢占"]
    WK["worker 上传"]
  end

  subgraph domain["domain 领域"]
    TASK["Task / 状态机"]
    SET["UploadSettings / SettingsHub"]
    PROG["UploadProgress"]
  end

  subgraph ports["ports 接口"]
    TRP["Transport"]
    AFT["AfterUpload"]
    RES["Rescheduler"]
    PREP["ProgressReporter"]
  end

  subgraph adapters["adapters 实现"]
    TEL["TelegramTransport"]
    SESS["SessionPool"]
    STORE["TaskRepository"]
    AFTER["keep / delete / archive"]
    HUB["ProgressHub + 日志条"]
  end

  subgraph 外部
    FS["监听目录"]
    TG["Telegram"]
    DB[(SQLite)]
  end

  MAIN --> APP
  UI --> API["FastAPI 控制台 API"]
  API --> APP
  APP --> DIS & ING & SCH & WK
  DIS --> FS
  ING --> TASK & SET
  SCH --> STORE
  WK --> TRP & AFT & PREP
  TRP --> TEL --> TG
  AFT --> AFTER
  PREP --> HUB
  HUB --> API
  STORE --> DB
  TEL --> SESS
```

依赖只能向下：`pipeline` 用 `domain` + `ports`，`adapters` 实现 `ports`。控制台只读进度/Worker、改 `upload.toml`，不投喂文件。

### 运行时

```mermaid
flowchart LR
  subgraph 进程["python -m src.main"]
    W[FolderWatcher]
    S[启动扫盘]
    Q[file_queue]
    I[FileIngestor]
    DB[(SQLite)]
    SC[Scheduler]
    WK[UploadWorker × N]
    HTTP[FastAPI :8000]
    HUB[ProgressHub]
  end

  DISK["observer_paths<br/>一个或多个目录"] --> W
  DISK --> S
  W --> Q --> I --> DB
  S --> Q
  DB --> SC --> WK
  WK --> TG[Telegram]
  WK --> HUB --> HTTP --> BROWSER["浏览器控制台"]
```

Session 文件在 `sessions/`：每 2 秒扫描，有则加载 Worker，没有则卸载。

### 任务状态

```mermaid
stateDiagram-v2
  [*] --> preparing: 需要封面
  [*] --> pending: 不需要封面
  preparing --> pending: 截图完成或失败
  pending --> assigned: Scheduler CAS 抢占
  assigned --> uploading: Worker 开始 send_file
  uploading --> success: 发出
  uploading --> pending: FloodWait / 未超限失败
  uploading --> failed: 超过 max_retries
  success --> [*]
  failed --> [*]
```

## 工作流程

```mermaid
flowchart TD
  A[.env + upload.toml + sessions/] --> B[UploaderApplication 启动]
  B --> C[启动对账: 幽灵任务打回 pending]
  C --> D[发现层]
  D --> D1[watchdog 新文件]
  D --> D2[启动扫盘已有文件]
  D1 --> E[file_queue]
  D2 --> E
  E --> F[FileIngestor]
  F --> F1[等写稳 / 去重 / 拍策略快照]
  F1 --> F2{要封面?}
  F2 -->|是| F3[status=preparing, ffmpeg 截图]
  F3 --> G[(SQLite pending)]
  F2 -->|否| G
  G --> H[Scheduler 原子抢占]
  H --> I[UploadWorker]
  I --> J[Telegram send_file]
  J -->|成功| K[AfterUpload: keep / delete / archive]
  J -->|FloodWait| L[回 pending, 不计失败]
  J -->|失败| M[回 pending 或 failed]
```

状态：`preparing → pending → assigned → uploading → success | failed`。失败未超次数回到 `pending`。

## 零件如何连起来

唯一的装配点是 `UploaderApplication`（`src/pipeline/application.py`）。各阶段 **不互相 import 对方的实现**，只通过下面几根线说话。

```mermaid
flowchart TB
  subgraph 配置
    ENV[".env → config.py<br/>API / 库路径 / session 目录"]
    TOML["upload.toml → SettingsHub"]
    SESS["sessions/*.session → SessionPool"]
  end

  subgraph 装配["UploaderApplication"]
    APP[run]
  end

  ENV --> APP
  TOML --> APP
  SESS --> APP

  APP --> W[FolderWatcher]
  APP --> SCAN[启动扫盘]
  W -->|Path| Q[file_queue]
  SCAN -->|Path| Q
  Q --> ING[FileIngestor]

  ING --> POL[IngestPolicy]
  ING --> TOP[TopicCreator]
  ING --> PREV[video_preview]
  ING --> STORE[TaskRepository]
  TOP --> STORE
  ING -->|request_reschedule| SCH[UploadScheduler]

  STORE --> DB[(SQLite)]
  SCH --> STORE
  SCH -->|claim 成功| WK[UploadWorker 队列]

  SESS --> POOL[SessionPool]
  POOL -->|TelegramClient| WK
  POOL -->|any_client| TOP
  WK --> TR[TelegramTransport]
  WK --> AFTER[AfterUpload]
  WK -->|request_reschedule| SCH
  WK -->|UploadProgress| HUB[ProgressHub]
  HUB -->|SSE| API["FastAPI /api/progress/stream"]
  API --> UI[web/ 或 Vue]
  AFTER --> STORE
  TR --> TG[Telegram]
```

### 谁创建谁（启动时）

`main.py` 只调用 `UploaderApplication().run()`。`run()` 里按这个顺序接线：

1. `init_db()` 建表。
2. `SettingsHub` 读 `upload.toml`（没有就从 example 复制）。
3. `TaskRepository` 对账：把上次挂掉的 `assigned` / `uploading` / `preparing` 打回 `pending`。
4. `SessionPool` 盯着 `sessions/`；`UploadScheduler` 拿着仓库 + 配置。
5. `TopicCreator(session_pool.any_client, repository)`：发话题时现取一个在线 client，session 被卸掉也不会握着死连接。
6. `FileIngestor(repository, scheduler, settings_hub, topic_creator)`：入库后用调度器的 `request_reschedule()` 叫醒分发。
7. `FolderWatcher`：watchdog 监听 `observer_paths`（可多条，热更新会重挂），只往队列丢路径。
8. 启动扫盘把已有视频也 `put` 进同一条队列。
9. `_sync_sessions`：每个授权成功的 session 做一个 `UploadWorker`，塞进 `scheduler.worker_map`，并在 TaskGroup 里跑 `serve_forever()`。

之后常驻四类循环：监听、ingest 消费队列、调度、每 2 秒热更新 toml + 再扫 session。

### 运行时三根线

| 线 | 连的是谁 | 传什么 |
|---|---|---|
| `file_queue` | Watcher / 扫盘 → Ingestor | 本地 `Path` |
| SQLite | Ingestor、Scheduler、Worker、TopicCreator | 任务状态（唯一真相） |
| `request_reschedule()` | Ingestor 入库后、Worker 做完一条后 → Scheduler | 无数据，只是叫醒「立刻再抢一轮」 |

Worker 自己还有一条 **内存任务队列**。调度器 CAS 抢到 `pending` 后 `enqueue_task`；Worker 不直接查「有没有新文件」。

Session 热插拔也是 Application 连的：磁盘多了 `.session` → `SessionPool.ensure_client` → `new UploadWorker(transport=TelegramTransport(client))` → `scheduler.register_worker`。文件没了则先 `unregister`，再 `worker.stop`，再断开 client。

### 配置怎么灌进去

- **过程参数**（并发、超时）：Scheduler / Worker 每次做事调 `settings_hub.get()`，改 toml 下一轮就生效。
- **任务参数**（删不删文件、重试次数、要不要封面）：Ingestor 入库时 `policy_for_new_task` 拷进这条任务；Worker 成功收尾读的是 `task.policy`，不是此刻的 toml。
- **发送目的地**：Ingestor 写入 `chat_id` / `topic_id`；`TelegramTransport` 发送时带 `reply_to=topic_id`。

### 接口和实现

`ports/` 只定义形状，`adapters/` 才碰 Telegram / 磁盘：

- Worker 依赖 `Transport` + `AfterUpload`，实际注入的是 `TelegramTransport` 和 `ConfigurableAfterUpload`。
- Ingestor 依赖 `Rescheduler`，实际注入的是 `UploadScheduler`（只调用 `request_reschedule`）。
- 换发送实现或换收尾策略，只换 Application 里那一次构造，不必改 Scheduler。
- Worker 上报 `UploadProgress` → `ProgressHub` 广播；FastAPI 用 SSE 推给前端，日志进度条是同一个事件的另一个订阅者。

## 进度与前端

板块：Workers、上传进度、创建 Session、上传配置。源码在 `frontend/`（Vue 3 + TypeScript + Element Plus），构建到 `frontend/dist`，由 FastAPI 托管。

启动后：

- 页面：http://127.0.0.1:8000/
- `GET /api/workers` worker 快照（含已禁用、未在跑的 session）
- `POST /api/workers/{name}/disable` 禁用（session 文件保留）
- `POST /api/workers/{name}/enable` 启用
- `DELETE /api/workers/{name}` 删除 session 文件
- `GET /api/progress` 进度快照
- `GET /api/progress/stream` SSE，事件名 `progress`
- `GET /api/settings` 当前 upload.toml
- `PUT /api/settings` 写入并立刻热加载
- `GET /api/sessions` 已有 session 文件名
- `POST /api/sessions/start|code|password` 在服务器上登录并写入 `sessions/`

开发（需同时跑 `python -m src.main`）：

```bash
cd frontend
npm install
npm run dev
```

Vite 把 `/api` 代理到 `127.0.0.1:8000`。改界面后：`npm run build` 再刷新 8000 端口的生产页。

`send_file(..., progress_callback)` 的进度经 Hub 节流后推 SSE（约 1% 或 0.4 秒一次），完成/失败立即推。

## 文件怎么进来

只认监听目录：把视频放到 `upload.toml` 的 `observer_paths`（默认 `download/`，可多条）。watchdog 发现后入库上传。没有 HTTP 投喂，也没有阻塞等待接口。

## 关键约定

- **SQLite 是真相源**。内存队列在进程被杀后会丢，启动时把 `assigned` / `uploading` / `preparing` 打回 `pending`。
- **策略快照**：入库时把「传完是否删文件、重试次数」拷进任务。之后改 `upload.toml` 只影响新文件。
- **FloodWait**：Telegram 限流，不是文件坏了。任务回队列且不增加失败次数，该 session 暂停接新活。
- **论坛话题**：创建话题后发送必须带 `reply_to=topic_id`，否则进群的 General。
- **Session**：`sessions/Homa.session` 的文件名就是 worker 名。放入或拿走文件约 2 秒后自动加载/卸载。命令行批量生成：`python -m src.adapters.generate_session`。

## 目录与文件

| 路径 | 作用 |
|---|---|
| `src/main.py` | 进程入口 |
| `src/config.py` | 系统配置：API、数据库路径、session 目录 |
| `src/logger.py` | 控制台 + 滚动文件日志 |
| `src/pipeline/application.py` | 装配各阶段、生命周期、热加载 session/配置 |
| `src/pipeline/discover/watcher.py` | watchdog 监听目录 |
| `src/pipeline/discover/scan.py` | 启动时扫已有文件 |
| `src/pipeline/ingest/policy.py` | 是否入库、要哪种封面 |
| `src/pipeline/ingest/ingestor.py` | 写稳、建话题、入库、截图 |
| `src/pipeline/schedule/scheduler.py` | 从 DB 抢 pending 分给 worker |
| `src/pipeline/worker.py` | 取任务并上传 |
| `src/domain/progress.py` | 进度事件结构 |
| `src/adapters/progress.py` | 进度总线 + 终端进度条 |
| `src/api/app.py` | FastAPI：workers、SSE、托管 Vue 构建产物 |
| `src/api/workers.py` | worker 列表快照 |
| `src/adapters/session_login.py` | 控制台创建 session |
| `src/adapters/generate_session.py` | 命令行批量生成 session：`python -m src.adapters.generate_session` |
| `frontend/` | Vue 3 + TS 界面 |
| `frontend/dist/` | 前端构建产物 |
| `src/domain/task.py` | 任务对象与状态 |
| `src/domain/upload_settings.py` | 可热更新的上传策略结构 |
| `src/domain/settings_hub.py` | 读/热更新 `upload.toml`，给任务拍策略快照 |
| `src/domain/concurrency.py` | 可随配置变化的并发闸门 |
| `src/ports/transport.py` | 发送接口：成功 / 限流 / 失败 |
| `src/ports/after_upload.py` | 上传成功后本地收尾接口 |
| `src/ports/rescheduler.py` | 唤醒调度器的接口 |
| `src/adapters/sessions.py` | 扫描并连接 `sessions/*.session` |
| `src/adapters/telegram_transport.py` | Telethon 实际发送 |
| `src/adapters/after_upload.py` | keep / delete / 归档 |
| `src/adapters/task_store.py` | SQLite 任务表读写 |
| `src/database/connection.py` | 打开 SQLite |
| `src/database/init.py` | 建表 |
| `src/utils/topic_creactor.py` | 按目录创建/复用论坛话题 |
| `src/utils/video_preview.py` | ffmpeg 首帧 / 网格封面 |
| `.env` / `.env.example` | API 等系统配置 |
| `upload.toml` / `upload.toml.example` | 上传策略 |
| `sessions/` | Telegram session 文件 |

启动：`python -m src.main`（在项目根目录）。进度条打开 http://127.0.0.1:8000/ 。

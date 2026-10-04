# uploader 技术日志

记录本仓库从「能跑的单机脚本」演进到「可热更新的目录上传服务」过程中的结构、决策和已知边界。能核对到代码的写事实，会话里讨论过但后来删掉的也注明。

---

## 1. 项目是什么

监听一个或多个本地目录，把视频（可选封面）用 Telethon 用户/Bot session 发到指定 Telegram 群。可选按子目录创建论坛话题。控制台是同进程 FastAPI + Vue。

**文件只从监听目录进。** 曾经做过 HTTP 投喂和阻塞等待，已按需求撤掉。

入口：`python -m src.main`  
控制台：`http://127.0.0.1:8000/`

---

## 2. 演进摘要

| 阶段 | 做了什么 |
|---|---|
| 评审 | 旧代码 happy path 能跑，崩溃不恢复、话题没用 `reply_to`、发现层漏文件、SQLite 裸写 |
| 重构 | 领域 Task、ports/adapters、`.env` vs `upload.toml` |
| 布局 | `src/upload` 打散为 `pipeline/{discover,ingest,schedule}` |
| 控制台 | FastAPI、SSE 进度、Vue 3 + TS + Element Plus |
| Session | 动态扫 `sessions/`；控制台登录；CLI 挪到 `src/adapters/generate_session.py` |
| 运维 | Worker 禁用/删除；断线退避重连；Ctrl+C 在 Windows 上曾假死，后加强制退出 |
| 监听 | 多路径、cwd 解析绝对路径、不自动建目录 |
| 入库并行 | 写稳/建话题移出全局锁，最多 8 个文件同时等写完；INSERT 仍串行去重 |
| 网格封面 | 按时长均分 15 段，`-ss` 在 `-i` 前 seek 附近关键帧，不再整片 `fps` 解码 |
| SQLite | 启动打开 4 条长连接小池；`(file_path, status)` / `(assigned_bot, status)` 索引 |
| 调度 | 负载只看库里 assigned+uploading；唤醒后 50ms 合并 `request_reschedule` |
| 路由 | `data/upload.toml` 的 `[[routes]]`；未命中不上传 |
| 上传 | FastTelethon：512KB 分片，每账号最多 6 条连接；短断线留在原账号续传 |
| 过大 | 超过 2_097_152_000 字节停在 `oversized`，不自动分给 Bot |
| 凭据 | `data/telegram.json` 热注入；没配凭据控制台也能开 |
| 镜像 | push `main` 只构建 `ghcr.io/aoki222/tg-up:staging`；晋级 `latest` 要手动跑 workflow |

曾单独 git：先归档 `upload_old` / 旧 handler，再提交新结构，再删旧代码。

---

## 3. 架构（当前）

```
发现(watchdog+扫盘) → 入库(并行写稳 / 话题 / INSERT)
                         ↘ PreviewPool（首帧/网格，不堵入库）
    → SQLite → 调度(CAS 抢占) → Worker(FastTelethon) → AfterUpload
                                      ↓
                                 ProgressHub → SSE → 控制台
```

依赖方向：`pipeline` → `domain` + `ports` ← `adapters`。

| 包 | 职责 |
|---|---|
| `src/domain` | Task、状态、UploadSettings、进度事件 |
| `src/ports` | Transport / AfterUpload / Rescheduler / ProgressReporter |
| `src/adapters` | Telethon、SQLite、session 池、登录、进度总线、禁用名单 |
| `src/pipeline` | 装配、发现、入库、调度、Worker |
| `src/api` | 控制台 HTTP，不投喂文件 |
| `frontend/` | Vue；构建到 `frontend/dist` |

SQLite 是任务真相源。内存队列在杀进程后会丢，启动必须对账。连接是启动时 4 条长连接，不是每次 SQL 新建。

---

## 4. 任务状态机

```
preparing → pending → assigned → uploading → success
                              ↘ 失败未超次数 / FloodWait → pending
                              ↘ 短断线：留在 uploading，原账号续传；5 次仍失败才回 pending
                              ↘ 超限 → failed
发现时已超过 Bot 分片上限 → oversized（封面可先停在 preparing）
```

- `preparing`：调度器看不见，等封面（失败也转 pending，只发视频）。文件本身已超过 Bot 上限时，封面做完转入 `oversized`，不进调度
- 没有 `retrying`（schema 里可能还有旧值，读出来当 pending）
- `claim_task` 带 `WHERE status IN ('pending','retrying')` 的 CAS。`oversized` 不在这张捞取里
- `oversized`：看板「过大」。只由 `POST /api/tasks/{id}/dispatch-user` 交给当前最空闲的个人号。没有个人号时 409，detail 是「没有可用的个人账号」。Bot 若拿到这种任务会立刻停回 `oversized`。个人号传超分片上限同样停回，不换 Bot
- 全部重试失败：缺文件的跳过；超过 Bot 上限的改成 `oversized`，不回到 Bot
- 源文件已经不在，或队列里仍是 yt-dlp 临时文件：一次记 `failed`，不再回队列

**策略快照：** 入库时把 `after_success` / `max_retries` / 是否封面拷进任务行（`upload_tasks.after_success` 列）。之后改 toml 不影响已入库任务。内存 `_policies` 只是加速，重启后从行上读。

---

## 5. 配置分层

| 文件 | 生命周期 | 内容 |
|---|---|---|
| `.env` | 启动读一次。监听地址、令牌、代理改完要重启 | 可选 `API_TOKEN`、`TELEGRAM_PROXY`（仅 http/https）、`API_HOST` / `API_PORT`。`API_ID` / `API_HASH` 只是没有 json 时的回落 |
| `data/telegram.json` | 控制台保存后热注入，不必重启 | 只存 `api_id`、`api_hash` |
| `data/upload.toml` | 热更新（约 2 秒，或 API 保存时立刻换内存） | 目标群、监听路径、`[[routes]]`、封面、删文件、超时。`concurrency` 固定写成 1 |

根目录若还有旧的 `upload.toml`，启动时复制进 `data/` 一次。Docker 挂的是整个 `./data`，避免单文件挂载导致保存 EBUSY。

**凭据：**

- 文件里的 `api_id` 和 `api_hash` 都有值时用文件，否则用环境变量，两边都没有则 `source=none`。缺凭据不阻止进程听端口
- `PUT /api/identity` 原子写入 json，再注入当前 `SessionPool` 和登录服务。空 hash、或带掩码圆点，保留现有 hash；结果不足 16 位则 400「API_HASH 长度不足」
- 值和当前相同：不断开。从空变成已配置：马上连接已有 session。换了 `API_ID` 或 `API_HASH`：卸掉已连接客户端再重连，**不删** `.session`。旧会话授权失败只记一次「该会话属于旧的 API_ID，需要重新登录」
- `GET /api/identity` 返回 `api_id`、掩码、`configured`、`source`，不返回完整 hash。公开的 `GET /api/settings` 也不含 hash
- 保存上传策略会整份重写 toml，所以凭据不能放进 `UploadSettings`
- `POST /api/process/restart` 还在。设置页保存凭据不再询问重启

Google Drive 的设置页签和路径目标组由 `frontend/src/features.ts` 的 `ENABLE_GOOGLE_DRIVE` 关掉（当前 `false`）。这是编译期常量，改完要重新构建前端。toml 里已有的 `gdrive` 路由仍然有效。

控制台保存 toml：无修改时禁用。

**监听路径规则（后改）：**

- 相对路径相对 **进程 cwd**，绝对路径原样 `resolve`
- 必须已存在且为目录；**不创建**文件夹
- 可多条；无效路径仍保存在配置里，前端标「目录不存在」，不挂 watchdog
- 写入 toml 时存绝对路径

封面目录 `page_dir`、归档目录仍相对项目根（未改成 cwd）。

---

## 6. 发现与入库

- Watchdog：`on_created` + `on_moved`（Windows 剪切）
- 启动扫盘 + 新加监听目录时补扫
- 写稳：第一次 `stat` 算一轮；`mtime` 已经久于剩余观察窗口（默认约 4s）的旧文件立刻过，不必再睡。正在拷贝的仍按「连续几次大小不变」等，有超时
- 扩展名白名单
- 未完成任务按绝对 `file_path` 去重；同一路径正在入库时用 `_inflight_paths` 丢掉重复 watchdog 事件
- **并行：** `INGEST_CONCURRENCY=8` 同时等写稳/建话题。锁只包「再查重 + INSERT」。`consume` 取出后 `create_task`，结束哨兵后等在途任务收尾
- 话题：`(chat_id, 目录绝对路径)` 复用；按这个键加锁，不同目录可并行 `CreateForumTopic`。库里已有记录时不占锁。发送必须 `reply_to=topic_id`，否则进 General
- 建话题时若还没有连上的客户端，最多等 20 秒。仍没有则记「暂无已连接的 Telegram session」，5 秒后把文件放回发现队列，不记失败
- 目录路由写在 toml 的 `[[routes]]`。未命中的文件不上传
- 临时文件：文件名里有 `.f` 或 `.temp` 才做正则。yt-dlp 的 `.f251.webm`、`.f251-1.webm`、`.temp.mp4` 不入库。快路径区分大小写，`.F251-1.WEBM` 不会被这条规则拦住
- 封面：需要时写成 `preparing` 立刻返回，截图在 `PreviewPool`（默认并发 2）。失败不丢视频：`page_path` 置空转 `pending`
- 网格封面：ffprobe 时长 → 15 段中点 `t_i = duration * (i+0.5)/15` → 串行 `ffmpeg -ss t_i -i … -frames:v 1`（输入侧 seek，附近关键帧）。读不到时长按 60s 间隔同样 seek，不再 `fps=15/duration` 整片解码。单格失败跳过；一张都没有则回退截第 1 秒

SQLite：`init_db()` 打开 4 条长连接（WAL / busy_timeout 只设一次），`get_db()` 借还，退出 `close_pool()`。`find_active_by_file_path` 走 `(file_path, status)` 索引。

调度：负载 = 库里该 worker 的 `assigned+uploading`（一条 `GROUP BY assigned_bot`），**不加**内存队列长度（claim 后任务已是 assigned，再加 `qsize` 会双计，并发 3 时 3 条未开工会被看成 6）。`request_reschedule()` 仍是随时 `Event.set()`；调度循环唤醒后睡 50ms 再 `clear`，一批入库合成一轮 `schedule_once`。`schedule_once` 期间新来的 set 下一圈会立刻再跑（必要）。`stop()` 跳过 50ms。

---

## 7. 上传与 Telegram

- 每 session 一个 Worker，同时只传 1 个任务；文件名即 worker 名
- 上传走 FastTelethon：同一 DC 的额外连接，分片 512KB，每个账号最多 6 条，闲置 120 秒收回。有封面时视频和图仍当相册
- 进度：`progress_callback` → ProgressHub（节流约 1% 或 0.4s）→ SSE。速度要间隔至少 0.2 秒且字节增加才更新，否则沿用上一笔；第一笔样本之前卡片显示「测算中」。相册的「文件 x/y」不进字节速度
- 看板每秒拉 `GET /api/tasks`，只改列和计数。字节、百分比、速度跟 SSE。进行中的字节条不被更旧的轮询或相册个数往回拨。任务回到等待后再被拿走，进度可以从 0 开始
- FloodWait：不计失败、该 worker 暂停接新活
- 上传中途断线：任务留在这个账号，库状态保持 `uploading`，卡片停在「上传中」，文案「正在重连」，字节保持断线前的数字。已完成分片按文件路径留在这个传输上，同一账号重连后接着传；另一个账号不能用这些分片。续传再失败也保留分片，不退回单连接 `send_file`。连着失败 5 次才 release 回 `pending`，并推「重连失败，已回队列」
- 还没有分片记录的普通 FastTelethon 失败：最多 2 次，然后才回退原生 `send_file`。`FilePartsInvalid` 记 `oversized`，不回退
- Bot 上限是 4000 × 512KB = 2_097_152_000 字节，不是十进制 2GB。入库时就超过的文件直接 `oversized`
- 会话级断线（这个账号当前没在传）：最多 5 次，等待 1→2→4→8→16 秒，上限 30 秒；5 次失败再等 30 秒开新一轮。前端标签「重连 n/5」
- 未授权 session 不按网络重试，直接跳过。凭据更换之后的未授权只记一次「该会话属于旧的 API_ID，需要重新登录」

**AfterUpload：** `keep` / `delete` / `move_to_archive`，看任务自己的 policy。归档按「落在哪条监听根下」保留相对目录。

---

## 8. Session

- 运行中扫 `sessions/*.session`，放入加载、拿走卸载；跳过 `_tmp_`
- 控制台「添加 Session」：Bot Token、手机号或二维码。验证码在 Telegram 应用里，不是短信。二维码路径是「设置 → 设备 → 关联桌面设备」，并按这张码的剩余时间倒计时。验证码和两步验证可以返回修改手机号
- 没配凭据时不能登录。`GET /api/sessions` 的 `api_configured` 看当前进程；未配置时 start 返回 400「请先在系统设置中配置 Telegram API_ID 与 API_HASH」
- 登录用的 `api_id` / `api_hash` 是当前进程里的凭据（json 优先，否则环境变量），不下发到浏览器
- 登录多步 HTTP：`/api/sessions/start|code|password`，二维码再加 `GET/DELETE /api/sessions/login/{id}`；成功后改名为 `<username>.session`
- CLI：`python -m src.adapters.generate_session`（从仓库根的 `generate_session.py` 挪入 adapters）
- SOCKS 代理已删（Pylance 缺模块）；代理仅 http/https（CLI 另保留 mtproxy）

**禁用 vs 删除：**

| | 禁用 | 删除 |
|---|---|---|
| 文件 | 保留 | 删 `.session` 及 journal |
| 名单 | `data/disabled_workers.json` | 从名单去掉 |
| 任务 | 队列 assigned 立刻 release；在途最多等 20s 再取消 | 同左 |

release：`pending`，清空 `assigned_bot` / `assigned_at` / `started_at`，**不 +retry_count**。

扫盘：磁盘有文件且未禁用才加载。只在内存里 `enabled=False` 会在 2 秒内被复活。

---

## 9. 控制台 API（当前）

- `GET /api/workers` 含禁用、重连字段
- `POST .../disable` `POST .../enable` `DELETE /api/workers/{name}`
- `GET/PUT /api/settings`
- `GET /api/tasks` 看板；`POST /api/tasks/{id}/retry`、`POST /api/tasks/retry-failed`
- `DELETE /api/tasks/{id}`、`POST /api/tasks/failed/delete`、`DELETE /api/tasks/failed`
- `GET /api/progress` + `GET /api/progress/stream`（SSE，事件名 `progress`）
- `GET/PUT /api/identity`（读写 `data/telegram.json` 并热注入）；`POST /api/process/restart`（设置页保存凭据不再调用）
- `POST /api/tasks/{id}/dispatch-user` 把过大文件交给个人号
- 登录相关 `/api/sessions/*`

已删除：`POST /api/tasks`（投喂/wait）、演示进度 `/api/debug/fake-progress`。

`API_TOKEN` 为空则 `/api/*` 全放行（本机单用）。设置后除 `GET /api/health` 外都要令牌：Axios 走 `Authorization: Bearer`；SSE 的 `EventSource` 不能自定义头，用 `?access_token=`。查询参数勿写入应用日志。静态页 `/`、`/assets` 不鉴权。

---

## 10. 启停（Windows 上踩过的坑）

目标：第一次 Ctrl+C 停入口、取消在途、release、断开；**整进程不等 60 秒**。

问题：把 SIGINT 换成「往事件循环塞回调」后，若卡在 `connect()` 等调用上，循环不跑回调，**第一次 Ctrl+C 像没按**。`main.py` 还曾吞掉 `KeyboardInterrupt`。

对策：

- 第一次 Ctrl+C 置停止事件；约 10 秒仍没退完则 `os._exit(1)`
- 再按一次在信号处理函数里直接强制退出
- `connect()` 超时 8 秒，避免启动同步把退出逻辑堵在门外
- 首轮 session 同步改到 runtime 循环，不阻塞等待退出信号

---

## 11. 明确做过又拿掉的

- HTTP 投喂本机路径 + `wait: true` 阻塞到上传结束（7-Zip 式给别的项目调）→ 只保留监听目录
- 假 SSE 演示进度按钮
- `src/upload_old`、旧 `handler.py`、`insert_single_task`、一度清空的 `tests/`
- 根目录 `generate_session.py`、`src/upload` 扁平包
- 根目录 `web/`（构建改到 `frontend/dist`）
- SOCKS 依赖
- 凭据只写 `.env`、保存后必须重启才能生效

`tests/` 后来加回：入库并行、话题锁、网格 seek、调度负载/合并唤醒、连接池。

---

## 12. 已知债

- 换监听路径已热挂 watchdog；`page_dir` / 归档目录仍是项目根相对路径（监听目录已改成 cwd）
- Vue 全量引入 Element Plus，产物偏大
- 禁用名单与 session 文件两套真相，要靠 `_sync_sessions` 对齐
- `SettingsHub._policies` 只增不删（重启后从行上读，内存字典长跑会涨）
- Worker 的 `TelegramClient` 未传 `TELEGRAM_PROXY`（控制台登录会传）
- AfterUpload 的 `unlink` / `shutil.move` 仍在事件循环线程上跑

已还掉的债：截图拆到 PreviewPool；网格改为关键帧 seek；写稳不再串行堵入库；WAL + 4 连接小池；`after_success` 进了任务行；`file_path` 有索引；调度负载不再双计队列；凭据热注入，不再为了改 API 重启。

---

## 13. 常用命令

```bash
python -m src.main
python -m src.adapters.generate_session
cd frontend && npm run dev      # 代理 /api → 8000
cd frontend && npm run build    # → frontend/dist
docker compose up -d --build    # 容器名 uploader，数据在 ./data
```

push `main` 构建并推送 `ghcr.io/aoki222/tg-up:staging`（改动只有 Markdown 时不触发）。换成 `latest` 要手动跑「Promote Staging to Latest」。测试机 compose 是 `docker-compose.staging.yml`，容器名 `uploader-staging`。

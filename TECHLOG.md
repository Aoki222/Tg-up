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

曾单独 git：先归档 `upload_old` / 旧 handler，再提交新结构，再删旧代码。

---

## 3. 架构（当前）

```
发现(watchdog+扫盘) → 入库(写稳/话题/截图) → SQLite
    → 调度(CAS 抢占) → Worker(send_file) → AfterUpload
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

SQLite 是任务真相源。内存队列在杀进程后会丢，启动必须对账。

---

## 4. 任务状态机

```
preparing → pending → assigned → uploading → success
                              ↘ 失败未超次数 / FloodWait / 断线 → pending
                              ↘ 超限 → failed
```

- `preparing`：调度器看不见，等封面（失败也转 pending，只发视频）
- 没有 `retrying`（schema 里可能还有旧值，读出来当 pending）
- `claim_task` 带 `WHERE status IN ('pending','retrying')` 的 CAS

**策略快照：** 入库时把 `after_success` / `max_retries` / 是否封面拷进任务。之后改 toml 不影响已入库任务（快照目前主要在内存，重启后 `after_success` 会退回当前配置）。

---

## 5. 配置分层

| 文件 | 生命周期 | 内容 |
|---|---|---|
| `.env` | 启动只读，改完重启 | `API_ID` / `API_HASH`、可选 `API_TOKEN`、`TELEGRAM_PROXY`（仅 http/https） |
| `upload.toml` | 热更新（约 2 秒或 API 立刻写盘） | 目标群、监听路径、封面、删文件、并发、超时 |

控制台保存按钮：无修改时禁用。

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
- 写稳：连续几次文件大小不变；有超时
- 扩展名白名单
- 未完成任务按绝对 `file_path` 去重
- 话题：`(chat_id, 目录绝对路径)` 复用；发送必须 `reply_to=topic_id`，否则进 General

截图仍在 ingest 协程里串行（首帧/网格），长视频会堵住后面入库。这是已知未拆完的结构债。

---

## 7. 上传与 Telegram

- 每 session 一个 Worker；文件名即 worker 名
- `send_file`：视频+封面当相册；`progress_callback` → ProgressHub（节流约 1% 或 0.4s）→ SSE
- FloodWait：不计失败、该 worker 暂停接新活
- 断线：最多 5 次，等待 1→2→4→8→16s，上限 30s；5 次失败再等 30s 开新一轮。前端标签「重连 n/5」
- 未授权 session 不按网络重试，直接跳过

**AfterUpload：** `keep` / `delete` / `move_to_archive`，看任务自己的 policy。归档按「落在哪条监听根下」保留相对目录。

---

## 8. Session

- 运行中扫 `sessions/*.session`，放入加载、拿走卸载；跳过 `_tmp_`
- 控制台「添加 Session」全屏模态：Bot Token 或手机号；API 身份只用服务端 `.env`
- 登录多步 HTTP：`/api/sessions/start|code|password`；成功后改名为 `<username>.session`
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
- `GET /api/progress` + `GET /api/progress/stream`（SSE，事件名 `progress`）
- 登录相关 `/api/sessions/*`

已删除：`POST /api/tasks`（投喂/wait）、演示进度 `/api/debug/fake-progress`。

`API_TOKEN` 为空不校验；设置后改配置、改 worker 需要 Bearer。

---

## 10. 启停（Windows 上踩过的坑）

目标：第一次 Ctrl+C 停入口、取消在途、release、断开；**整进程不等 60 秒**。

问题：把 SIGINT 换成「往事件循环塞回调」后，若卡在 `connect()` 等调用上，循环不跑回调，**第一次 Ctrl+C 像没按**。`main.py` 还曾吞掉 `KeyboardInterrupt`。

对策：

- 第一次 Ctrl+C 置停止事件；约 2 秒仍没退完则 `os._exit(1)`
- 再按一次在信号处理函数里直接强制退出
- `connect()` 超时 8 秒，避免启动同步把退出逻辑堵在门外
- 首轮 session 同步改到 runtime 循环，不阻塞等待退出信号

---

## 11. 明确做过又拿掉的

- HTTP 投喂本机路径 + `wait: true` 阻塞到上传结束（7-Zip 式给别的项目调）→ 只保留监听目录
- 假 SSE 演示进度按钮
- `src/upload_old`、旧 `handler.py`、`insert_single_task`、失效测试
- 根目录 `generate_session.py`、`src/upload` 扁平包
- `tests/`（只剩 pycache）、`web/`（构建改到 `frontend/dist`）
- SOCKS 依赖

---

## 12. 已知债

- 截图仍堵在 ingest 串行协程
- `after_success` 快照未进 SQLite，重启后跟当前 toml
- 未开 SQLite WAL，高并发可能 `database is locked`
- 换监听路径已热挂 watchdog；`page_dir` 等仍是项目根相对路径
- Vue 全量引入 Element Plus，产物偏大
- 禁用名单与 session 文件两套真相，要靠 `_sync_sessions` 对齐

---

## 13. 常用命令

```bash
python -m src.main
python -m src.adapters.generate_session
cd frontend && npm run dev      # 代理 /api → 8000
cd frontend && npm run build    # → frontend/dist
```

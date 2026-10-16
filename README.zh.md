# Tg-up

把监听目录里的视频，按目录策略发到 Telegram，并在网页上看进度和结果。

[English](README.md)

## 能做什么

**监听。** 可以监听多个目录，每个目录能单独暂停。新出现的文件和启动时扫到的文件都会入库。临时文件会被跳过，同一份文件不会重复上传。正在录制的目录可以单独设置落稳等待，文件大小连续安静够指定的秒数后才入库。

**投递。** 每个目录可以单独指定群、话题、说明、封面和落稳秒数。子目录沿用最近的上级策略。没有命中任何策略的文件不会上传。

**上传。** 多个 Telegram 账号分摊任务。遇到限流时，按 Telegram 要求的时间暂停该账号，任务回到队列。失败的任务可以重试。超过 Bot 上限的文件停在看板上，也可以按设置自动切片。排队顺序有四种：入库顺序、自然排序、创建时间、文件大小。

**网页。** 看板显示任务进度。监听、路由和上传策略在页面里修改后立即生效。Telegram 账号可以在网页上登录。

## 使用

在放有 `compose.yml` 的目录里：

```bash
cp .env.example .env
docker compose pull
docker compose up -d
```

编辑 `.env`，至少填入从 [my.telegram.org](https://my.telegram.org) 取得的 `API_ID` 和 `API_HASH`。`API_TOKEN` 是访问接口的口令。填上以后，除探活和版本号外，所有 `/api` 请求都要带它。网页仍可直接打开，在设置里填入同一串，浏览器会记住。Docker 默认对所有网卡开放 8421，这时必须填写，否则服务不会启动。留空只适用于只监听本机。

打开 `http://<主机>:8421`。第一次使用时，加上一个监听目录并打开它，再为它指定目标群。把 Session 放到旁边的 `sessions/` 目录，或在网页里登录。

更新时重新拉取并启动：

```bash
docker compose pull
docker compose up -d
```

这些目录和 `compose.yml` 放在一起。容器启动时，还没有的目录会建出来：

| 目录 | 内容 |
| --- | --- |
| `download` | 默认监听目录 |
| `sessions` | Telegram Session |
| `data` | 数据库和 `upload.toml` |
| `page` | 封面图 |
| `uploaded` | 上传成功后归档的文件 |
| `logs` | 日志 |

## 配置放在哪

`.env` 里是进程身份：`API_ID`、`API_HASH`、`API_TOKEN`、可选的 `TELEGRAM_PROXY`，以及目录策略没有指定目标群时使用的 `TARGET_CHAT_ID`。

监听目录、目录策略、封面、上传成功后的处理、上传顺序在网页里修改，写入 `data/upload.toml`，保存后生效，不用重启。

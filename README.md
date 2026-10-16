# Tg-up

Send videos from watched folders to Telegram according to per-folder rules, and follow the progress in a web page.

[中文](README.zh.md)

## What it does

**Watch.** Several folders can be watched, and each one can be paused on its own. New files and files found by the startup scan both enter the queue. Temporary files are skipped, and the same file is not uploaded twice. A folder that is still being recorded can wait until the file size stays unchanged for a chosen number of seconds.

**Delivery.** Each folder can set its own chat, topic, caption, preview, and settle time. A subfolder uses the nearest parent rule. A file that matches no rule is not uploaded.

**Upload.** Several Telegram accounts share the work. When Telegram rate-limits an account, that account waits for the requested time and the task returns to the queue. Failed tasks can be retried. A file over the bot size limit stays on the board, or is split into parts when that option is on. Pending tasks can be ordered by ingest order, natural name, creation time, or file size.

**Web.** The board shows live progress. Watch folders, routing, and upload policy take effect as soon as they are saved. Accounts can be signed in from the page.

## Usage

From the directory that contains `compose.yml`:

```bash
cp .env.example .env
docker compose pull
docker compose up -d
```

Edit `.env` and set `API_ID` and `API_HASH` from [my.telegram.org](https://my.telegram.org). `API_TOKEN` is the password for the API. When it is set, every `/api` request except the health and version checks must carry it. The page itself still opens; enter the same value in Settings and the browser remembers it. Docker listens on every network interface, so the token is required or the service will not start. Leave it empty only when the console listens on localhost.

Open `http://<host>:8421`. On the first visit, add a watch folder, turn it on, and choose a destination chat. Put a session file in the `sessions/` directory next to `compose.yml`, or sign in from the page.

To update:

```bash
docker compose pull
docker compose up -d
```

These directories sit next to `compose.yml`. The container creates any that are missing:

| Directory | Contents |
| --- | --- |
| `download` | Default watch folder |
| `sessions` | Telegram sessions |
| `data` | Database and `upload.toml` |
| `page` | Preview images |
| `uploaded` | Files archived after a successful upload |
| `logs` | Logs |

## Where settings live

`.env` holds process identity: `API_ID`, `API_HASH`, `API_TOKEN`, optional `TELEGRAM_PROXY`, and `TARGET_CHAT_ID`, the default chat used when a folder rule does not name one.

Watch folders, folder rules, previews, the action after a successful upload, and queue order are edited in the web page. They are stored in `data/upload.toml` and apply without a restart.

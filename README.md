# Telegram Video Uploader

A lightweight, efficient, and fully automated workspace for batch uploading and routing videos to Telegram.

No manual intervention required: simply drop your video files into the watched directory, and the system automatically discovers them, extracts preview thumbnails, routes them according to custom directory policies, and uploads them concurrently across multiple Telegram accounts to target channels or groups — all monitored in real time via a sleek, modern Web dashboard.

---

## Key Features & Highlights

### 1. Automated Directory Watching ("Drop and Upload")
- **Seamless Automation**: Monitors one or more local or remote server directories. Whether writing new files in real time or scanning existing files, it handles write-stability detection, duplicate filtering, and automated ingest without missing a beat.
- **Zero Manual Effort**: Eliminates tedious manual drag-and-drop uploads via Telegram Desktop or Web clients. Pairs seamlessly with downloaders like Aria2, qBittorrent, and yt-dlp to form an end-to-end automated pipeline.

### 2. Multi-Account Parallelism & Load Balancing
- **Multi-Session Coordination**: Drop multiple Telegram `.session` files into the `sessions/` directory, and the system automatically launches dedicated upload workers for each account.
- **Dynamic Hot-Plugging**: Add or remove session files on the fly at any time. The system detects and mounts/unmounts workers within seconds without restarting the service.
- **Smart Round-Robin Scheduling**: Dispatches tasks across active workers based on current load, maximizing bandwidth utilization and substantially boosting upload throughput.

### 3. Intelligent Rate-Limit Protection & Fault Tolerance
- **FloodWait Shield**: When hitting Telegram rate limits, the system automatically pauses the affected session for the exact cool-down window requested by Telegram, re-queuing the task without marking it failed or endangering the account.
- **SQLite State Persistence**: Task states are durably tracked in an atomic SQLite database. After service restarts or unexpected crashes, it reconciles tasks and resumes without missed or duplicated uploads.
- **Configurable Retries & Manual Resend**: Sets maximum retry limits and provides a one-click manual retry button in the Web console for tasks interrupted by transient network errors.

### 4. Flexible Folder Routing & Forum Topics Support
- **Folder-Based Multi-Channel Routing**: Route videos in different subdirectories to different target Telegram groups or channels based on path rules.
- **Automatic Forum Topics**: Automatically creates and manages Telegram Forum Topics in Supergroups based on folder names or rules, keeping channels and groups neatly organized.

### 5. Automated Thumbnails & Grid Previews
- **Built-in FFmpeg Processing**: Automatically extracts crisp single-frame video covers or generates multi-timestamp composite grid contact sheets for quick content previews.

### 6. Apple-Inspired Sleek Web Dashboard
- **Kanban Task Board**: Full real-time visibility across four core task states: *Preparing*, *Pending*, *Uploading*, and *Failed*, with collapsible columns and batch operations.
- **Real-Time Telemetry & SSE Streaming**: Ultra-smooth progress bars, sliding-window speed calculation, estimated time of arrival (ETA), and daily/cumulative success counters.
- **Visual Settings with Hot-Reloading**: Update upload policies and routing rules on the fly with instant effect—no service restart required. Includes web-based Telegram login and credential management.

### 7. Post-Upload Automation & Disk Cleanup
- **Post-Upload Action**: Automatically keep, delete, or archive the source video to a designated directory upon successful upload, preventing disk space exhaustion.

---

## Core Architecture & Modules

| Module | Location | Description |
|---|---|---|
| **Discovery** | `src/pipeline/discover/` | Monitors directory changes and performs startup scans, dispatching discovered videos to the internal ingest pipeline. |
| **Ingest** | `src/pipeline/ingest/` | Ensures file write stability, deduplicates files, matches destination groups/topics, and invokes FFmpeg for preview generation. |
| **Scheduler** | `src/pipeline/schedule/` | Atomically claims ready tasks from the database and distributes them via round-robin across available workers. |
| **Worker** | `src/pipeline/worker.py` | Orchestrates chunked Telegram uploads, reports real-time progress events, and executes post-upload cleanup/archival. |
| **Adapters** | `src/adapters/` | Manages the Telegram session pool, handling hot-plugging, multi-account concurrency, and transport abstraction. |
| **Web Dashboard** | `frontend/` + `src/api/` | Provides an intuitive visual Kanban board, real-time SSE telemetry, account management, and live configuration editor. |

---

## Quick Start

### 1. Environment Setup

Copy the sample environment file:

```bash
cp .env.example .env
```

Fill in your Telegram API credentials in `.env`:
- `API_ID` & `API_HASH` (Obtain from [my.telegram.org](https://my.telegram.org))
- `TARGET_CHAT_ID` (Default Telegram group or channel ID to receive uploads)
- `API_TOKEN` (Optional, protects the Web Dashboard API)

### 2. Prepare Telegram Account Sessions

Place your Telegram `.session` files (e.g., `my_account.session`) into the `sessions/` directory.  
Alternatively, generate a session interactively using the built-in CLI login tool:

```bash
python -m src.adapters.generate_session
```

### 3. Start the Service

#### Option A: Docker Deployment (Recommended)

```bash
mkdir -p download sessions data page uploaded logs
docker compose up -d --build
```

#### Option B: Local Python Environment

```bash
# Start the main service
python -m src.main
```

### 4. Access the Web Dashboard

Once the service is running, open your browser and navigate to:

```
http://localhost:8000
```

From the dashboard, you can monitor live upload tasks, view transfer speeds, manage accounts, and adjust upload policies and routing rules in real time.

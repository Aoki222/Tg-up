"""健康检查和版本药丸。版本接口不鉴权，结果在进程内缓存 15 分钟。"""

from __future__ import annotations

import asyncio
import os
import time

from fastapi import FastAPI

from .. import version


def register(app: FastAPI) -> None:
    # 进程是否在听。不鉴权，给探活用。
    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True}

    # 当前版本与镜像标签比较（15 分钟内存缓存，不鉴权）
    @app.get("/api/system/version")
    async def get_system_version() -> dict:
        current_raw = (os.getenv("APP_VERSION") or "local_dev").strip()
        is_dev = current_raw in ("local_dev", "dev", "")
        current_version = "dev" if is_dev else (current_raw[:7] if len(current_raw) >= 7 else current_raw)

        channel = (os.getenv("APP_CHANNEL") or "latest").strip().lower()
        if channel not in ("staging", "latest"):
            channel = "latest"

        if is_dev:
            return {
                "current_version": "dev",
                "remote_version": None,
                "has_update": False,
                "channel": channel,
                "status": "dev",
                "commit_message": "",
                "commit_url": "",
            }

        now = time.monotonic()
        cached_data = version._version_cache.get("data")
        last_check = float(version._version_cache.get("checked_at") or 0.0)
        if cached_data is not None and (now - last_check) < version._VERSION_CACHE_TTL:
            return dict(cached_data)

        remote_json = await asyncio.to_thread(version._fetch_remote_release_sync, channel)
        if not remote_json:
            result = {
                "current_version": current_version,
                "remote_version": None,
                "has_update": False,
                "channel": channel,
                "status": "staging" if channel == "staging" else "latest",
                "commit_message": "",
                "commit_url": f"https://github.com/{version._GITHUB_REPO}/pkgs/container/tg-up",
            }
            if cached_data is not None:
                return dict(cached_data)
            return result

        remote_sha = str(remote_json.get("sha") or "")
        remote_short = remote_sha[:7] if len(remote_sha) >= 7 else remote_sha
        commit_info = remote_json.get("commit") or {}
        raw_msg = str(commit_info.get("message") or "")
        commit_msg = raw_msg.splitlines()[0][:160] if raw_msg else ""
        html_url = str(remote_json.get("html_url") or f"https://github.com/{version._GITHUB_REPO}/commit/{remote_sha}")

        has_update = bool(remote_sha) and not remote_sha.startswith(current_raw) and not current_raw.startswith(remote_sha)

        if has_update:
            status = "update"
        elif channel == "staging":
            status = "staging"
        else:
            status = "latest"

        result = {
            "current_version": current_version,
            "remote_version": remote_short,
            "has_update": has_update,
            "channel": channel,
            "status": status,
            "commit_message": commit_msg,
            "commit_url": html_url,
        }
        version._version_cache["checked_at"] = now
        version._version_cache["data"] = result
        return result

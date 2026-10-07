"""比较本机镜像提交号和 GHCR 上的标签。不拉镜像，也不访问数据库。"""

from __future__ import annotations

import json
import urllib.request

from ..logger import get_logger

logger = get_logger(__name__)

_version_cache: dict[str, object] = {
    "checked_at": 0.0,
    "data": None,
}
_VERSION_CACHE_TTL = 900.0
_GHCR_REPO = "aoki222/tg-up"
_GITHUB_REPO = "Aoki222/Tg-up"
_MANIFEST_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
logger = get_logger(__name__)


def _http_json(url: str, headers: dict[str, str], timeout: float = 5.0) -> dict | None:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _child_manifest_digest(index: dict) -> str | None:
    """多架构清单里取 linux/amd64；没有就用第一条。"""
    manifests = index.get("manifests")
    if not isinstance(manifests, list):
        return None
    chosen: dict | None = None
    for item in manifests:
        if not isinstance(item, dict):
            continue
        platform = item.get("platform") or {}
        if (
            isinstance(platform, dict)
            and platform.get("os") == "linux"
            and platform.get("architecture") == "amd64"
        ):
            chosen = item
            break
        if chosen is None:
            chosen = item
    if chosen is None:
        return None
    digest = str(chosen.get("digest") or "")
    return digest or None


def _label_revision(config: dict) -> str | None:
    inner = config.get("config")
    labels = inner.get("Labels") if isinstance(inner, dict) else None
    if not isinstance(labels, dict):
        return None
    revision = str(labels.get("org.opencontainers.image.revision") or "").strip()
    return revision or None


def _ghcr_revision(tag: str) -> str | None:
    """读取 ghcr.io 上某个标签的镜像提交号。标签没动过就说明这台机器不用更新。"""
    token_payload = _http_json(
        f"https://ghcr.io/token?service=ghcr.io&scope=repository:{_GHCR_REPO}:pull",
        {"User-Agent": "Tg-up-App"},
    )
    token = str((token_payload or {}).get("token") or "")
    if not token:
        return None
    headers = {
        "User-Agent": "Tg-up-App",
        "Authorization": f"Bearer {token}",
        "Accept": _MANIFEST_ACCEPT,
    }
    manifest = _http_json(f"https://ghcr.io/v2/{_GHCR_REPO}/manifests/{tag}", headers)
    if manifest is None:
        return None
    if manifest.get("manifests"):
        digest = _child_manifest_digest(manifest)
        if not digest:
            return None
        manifest = _http_json(f"https://ghcr.io/v2/{_GHCR_REPO}/manifests/{digest}", headers)
        if manifest is None:
            return None
    config_digest = str((manifest.get("config") or {}).get("digest") or "")
    if not config_digest:
        return None
    blob = _http_json(
        f"https://ghcr.io/v2/{_GHCR_REPO}/blobs/{config_digest}",
        {
            "User-Agent": "Tg-up-App",
            "Authorization": f"Bearer {token}",
            "Accept": "application/octet-stream",
        },
    )
    return _label_revision(blob or {})


def _github_commit(ref: str) -> dict | None:
    return _http_json(
        f"https://api.github.com/repos/{_GITHUB_REPO}/commits/{ref}",
        {"Accept": "application/vnd.github+json", "User-Agent": "Tg-up-App"},
    )


def _fetch_remote_release_sync(channel: str) -> dict | None:
    """正式版看 latest 镜像，测试版看 staging 镜像。测试版在仓库读失败时才退回 main。"""
    tag = "staging" if channel == "staging" else "latest"
    sha = _ghcr_revision(tag)
    if sha:
        commit = _github_commit(sha)
        if commit and commit.get("sha"):
            return commit
        return {
            "sha": sha,
            "commit": {"message": ""},
            "html_url": f"https://github.com/{_GITHUB_REPO}/commit/{sha}",
        }
    if tag == "staging":
        return _github_commit("main")
    logger.warning("读取 latest 镜像版本失败，暂不提示更新")
    return None

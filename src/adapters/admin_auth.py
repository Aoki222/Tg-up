"""控制台唯一管理员：bcrypt 密码、SQLite session。

库是空的时候写入固定初始账号。must_change_password 为 1 时，
除改密和退出外，其它接口都不放行。
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Request, Response

from ..database.connection import get_db
from ..logger import get_logger

logger = get_logger(__name__)

COOKIE_NAME = "tgup_sid"
SESSION_TTL = timedelta(days=7)
RENEW_IF_LEFT = timedelta(days=1)
BCRYPT_ROUNDS = 12
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")
MIN_PASSWORD_CHARS = 8
MAX_PASSWORD_BYTES = 72

# 只在第一次建库时使用。改密成功后不再接受这对口令。
INITIAL_USERNAME = "admin"
INITIAL_PASSWORD = "admin"

_DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt(rounds=BCRYPT_ROUNDS))


@dataclass(frozen=True)
class AdminSession:
    user_id: int
    username: str
    session_id: int
    must_change_password: bool


def validate_username(raw: str) -> str:
    name = (raw or "").strip()
    if not USERNAME_RE.fullmatch(name):
        raise ValueError("用户名须为 3–32 位字母、数字或下划线")
    return name


def validate_password(raw: str) -> str:
    if not isinstance(raw, str) or len(raw) < MIN_PASSWORD_CHARS:
        raise ValueError(f"密码至少 {MIN_PASSWORD_CHARS} 位")
    encoded = raw.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise ValueError(f"密码不能超过 {MAX_PASSWORD_BYTES} 字节")
    return raw


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def hash_password(password: str) -> str:
    payload = password.encode("utf-8")

    def _run() -> str:
        return bcrypt.hashpw(payload, bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")

    return await asyncio.to_thread(_run)


async def check_password(password: str, stored: str) -> bool:
    payload = password.encode("utf-8")
    hashed = stored.encode("ascii")

    def _run() -> bool:
        try:
            return bool(bcrypt.checkpw(payload, hashed))
        except ValueError:
            return False

    return await asyncio.to_thread(_run)


async def dummy_check(password: str) -> None:
    """用户不存在时也跑一次校验，避免靠耗时判断账号是否存在。"""
    await check_password(password, _DUMMY_HASH.decode("ascii"))


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    real_ip = request.headers.get("x-real-ip")
    if real_ip and real_ip.strip():
        return real_ip.strip()
    return (request.client.host if request.client else "") or ""


async def owner_row() -> dict | None:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT username, password_hash, must_change_password FROM admin_users WHERE id = 1",
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return {
        "username": str(row["username"]),
        "password_hash": str(row["password_hash"]),
        "must_change_password": bool(row["must_change_password"]),
    }


async def ensure_initial_admin() -> None:
    """没有管理员时写入 admin / admin，并要求首次登录后改密。

    初始密码短于日后自设密码的长度限制，只用于这一次登录。
    已经建过、但还没改掉初始密码的库，把口令对齐到当前初始值。
    """
    validate_username(INITIAL_USERNAME)
    row = await owner_row()
    now = _utc_now().isoformat(sep=" ", timespec="seconds")
    if row is None:
        hashed = await hash_password(INITIAL_PASSWORD)
        async with get_db() as db:
            cursor = await db.execute("SELECT 1 FROM admin_users WHERE id = 1")
            if await cursor.fetchone() is not None:
                return
            await db.execute(
                """INSERT INTO admin_users
                   (id, username, password_hash, must_change_password, created_at, updated_at)
                   VALUES (1, ?, ?, 1, ?, ?)""",
                (INITIAL_USERNAME, hashed, now, now),
            )
            await db.commit()
        logger.warning("已写入初始控制台账号 %s。首次登录后必须修改密码。", INITIAL_USERNAME)
        return
    if not row["must_change_password"]:
        return
    if await check_password(INITIAL_PASSWORD, row["password_hash"]):
        return
    hashed = await hash_password(INITIAL_PASSWORD)
    async with get_db() as db:
        await db.execute(
            "UPDATE admin_users SET password_hash = ?, updated_at = ? WHERE id = 1 AND must_change_password = 1",
            (hashed, now),
        )
        await db.commit()
    logger.warning("尚未完成首次改密，初始密码已对齐为当前初始值。")


async def verify_login(username: str, password: str) -> str | None:
    name = (username or "").strip()
    row = await owner_row()
    if row is None:
        await dummy_check(password or "")
        return None
    if name.casefold() != row["username"].casefold():
        await dummy_check(password or "")
        return None
    if not await check_password(password or "", row["password_hash"]):
        return None
    return row["username"]


async def issue_session(username: str, request: Request) -> str:
    raw = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw)
    now = _utc_now()
    expires = now + SESSION_TTL
    async with get_db() as db:
        await db.execute(
            """INSERT INTO admin_sessions
               (user_id, token_hash, created_at, expires_at, last_seen_at, ip, user_agent)
               VALUES (1, ?, ?, ?, ?, ?, ?)""",
            (
                token_hash,
                now.isoformat(sep=" ", timespec="seconds"),
                expires.isoformat(sep=" ", timespec="seconds"),
                now.isoformat(sep=" ", timespec="seconds"),
                client_ip(request),
                (request.headers.get("user-agent") or "")[:300],
            ),
        )
        await db.commit()
    del username
    return raw


async def purge_expired() -> None:
    now = _utc_now().isoformat(sep=" ", timespec="seconds")
    async with get_db() as db:
        await db.execute("DELETE FROM admin_sessions WHERE expires_at < ?", (now,))
        await db.commit()


async def load_session(raw: str | None) -> AdminSession | None:
    if not raw:
        return None
    await purge_expired()
    token_hash = _hash_token(raw)
    now = _utc_now()
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT s.id, s.expires_at, u.username, u.must_change_password
               FROM admin_sessions s
               JOIN admin_users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND s.user_id = 1""",
            (token_hash,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        expires = datetime.fromisoformat(str(row["expires_at"]))
        if expires <= now:
            await db.execute("DELETE FROM admin_sessions WHERE id = ?", (row["id"],))
            await db.commit()
            return None
        new_expires = expires
        if expires - now < RENEW_IF_LEFT:
            new_expires = now + SESSION_TTL
        await db.execute(
            """UPDATE admin_sessions
               SET last_seen_at = ?, expires_at = ?
               WHERE id = ?""",
            (
                now.isoformat(sep=" ", timespec="seconds"),
                new_expires.isoformat(sep=" ", timespec="seconds"),
                row["id"],
            ),
        )
        await db.commit()
    return AdminSession(
        user_id=1,
        username=str(row["username"]),
        session_id=int(row["id"]),
        must_change_password=bool(row["must_change_password"]),
    )


async def revoke_session(raw: str | None) -> None:
    if not raw:
        return
    token_hash = _hash_token(raw)
    async with get_db() as db:
        await db.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (token_hash,))
        await db.commit()


async def revoke_all_sessions() -> None:
    async with get_db() as db:
        await db.execute("DELETE FROM admin_sessions WHERE user_id = 1")
        await db.commit()


async def change_password(current: str, new_password: str, username: str | None = None) -> str:
    """改密码。传入用户名时一并改名，供首次登录那一步使用。返回最终用户名。"""
    row = await owner_row()
    if row is None:
        raise FileNotFoundError("尚未创建管理员")
    if not await check_password(current, row["password_hash"]):
        raise PermissionError("当前密码不正确")
    secret = validate_password(new_password)
    if secrets.compare_digest(secret, INITIAL_PASSWORD) or await check_password(secret, row["password_hash"]):
        raise ValueError("新密码不能与初始密码或当前密码相同")
    name = row["username"]
    if username is not None and username.strip():
        candidate = validate_username(username)
        if candidate != row["username"]:
            name = candidate
    hashed = await hash_password(secret)
    now = _utc_now().isoformat(sep=" ", timespec="seconds")
    async with get_db() as db:
        await db.execute(
            """UPDATE admin_users
               SET username = ?, password_hash = ?, must_change_password = 0, updated_at = ?
               WHERE id = 1""",
            (name, hashed, now),
        )
        await db.execute("DELETE FROM admin_sessions WHERE user_id = 1")
        await db.commit()
    return name


async def change_username(current_password: str, username: str) -> str:
    """改控制台用户名。Session 按 user_id 关联，改名后不用重新登录。"""
    row = await owner_row()
    if row is None:
        raise FileNotFoundError("尚未创建管理员")
    if not await check_password(current_password, row["password_hash"]):
        raise PermissionError("当前密码不正确")
    name = validate_username(username)
    if name == row["username"]:
        raise ValueError("用户名没有变化")
    now = _utc_now().isoformat(sep=" ", timespec="seconds")
    async with get_db() as db:
        await db.execute(
            "UPDATE admin_users SET username = ?, updated_at = ? WHERE id = 1",
            (name, now),
        )
        await db.commit()
    return name


def attach_session_cookie(response: Response, raw: str, request: Request) -> None:
    proto = request.headers.get("x-forwarded-proto", "").lower()
    is_https = request.url.scheme == "https" or proto == "https"
    response.set_cookie(
        key=COOKIE_NAME,
        value=raw,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=is_https,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


def cookie_from(request: Request) -> str | None:
    value = request.cookies.get(COOKIE_NAME)
    return value or None

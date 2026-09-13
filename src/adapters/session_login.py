"""控制台创建 session：落盘约定与 generate_session CLI 相同，交互改成 HTTP 多步。

为什么不在浏览器里跑 Telethon：
- api_id / api_hash 是进程身份，不能下发到前端。
- 用户登录要同一条 TelegramClient 上连续 send_code → sign_in → 两步密码。
- 生成的 sqlite session 必须落在本机 sessions/，现有 SessionPool 每 2 秒扫盘就会加载成 Worker。

结构：
- 临时文件 _tmp_<id>.session，登录成功再改名为 <username>.session，避免半成品被扫描进 Worker。
- Bot 一步完成；用户号分 start / code / password 三步，pending 存在内存，超时清掉。
- 绑定群组可选：登录后 get_entity，失败只警告，session 仍保存（和 CLI 一致）。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from telethon import TelegramClient
from telethon.errors import (
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    RPCError,
    SessionPasswordNeededError,
)

from ..logger import get_logger

logger = get_logger(__name__)

_PENDING_TTL_SECONDS = 300.0


def sqlite_path(base: Path) -> Path:
    return base if base.suffix == ".session" else Path(str(base) + ".session")


def unlink_session(base: Path) -> None:
    target = sqlite_path(base)
    for path in (target, Path(str(target) + "-journal"), Path(str(target) + "-wal"), Path(str(target) + "-shm")):
        if path.is_file():
            path.unlink()


def parse_proxy(proxy_url: str | None):
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url.strip().strip('"').strip("'"))
    scheme = (parsed.scheme or "").lower()
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port
    if not port:
        return None
    if scheme in ("http", "https"):
        return ("http", host, port)
    return None


@dataclass
class LoginResult:
    done: bool
    step: str
    login_id: str | None = None
    name: str | None = None
    username: str | None = None
    is_bot: bool = False
    group_ok: bool | None = None
    group_error: str | None = None
    message: str = ""


@dataclass
class PendingLogin:
    login_id: str
    client: TelegramClient
    tmp_base: Path
    phone: str
    group_id: int | None
    force: bool
    created_at: float = field(default_factory=time.monotonic)


class SessionLoginService:
    """一次只推进一个 pending 用户登录；Bot 登录不占 pending。"""

    def __init__(self, session_dir: Path, api_id: int, api_hash: str, proxy_url: str | None = None):
        self.session_dir = session_dir
        self.api_id = api_id
        self.api_hash = api_hash
        self.proxy = parse_proxy(proxy_url)
        self._pending: dict[str, PendingLogin] = {}
        self._lock = asyncio.Lock()

    def list_saved(self) -> list[str]:
        if not self.session_dir.is_dir():
            return []
        names = []
        for path in sorted(self.session_dir.glob("*.session")):
            if path.stem.startswith("_tmp_"):
                continue
            names.append(path.stem)
        return names

    async def start_bot(self, bot_token: str, group_id: int | None, force: bool) -> LoginResult:
        token = bot_token.strip()
        if ":" not in token:
            raise ValueError("Bot Token 格式应为 <id>:<secret>")
        return await self._login_and_save(bot_token=token, group_id=group_id, force=force)

    async def start_user(self, phone: str, group_id: int | None, force: bool) -> LoginResult:
        normalized = phone.replace(" ", "").replace("-", "")
        if normalized and not normalized.startswith("+"):
            normalized = "+" + normalized
        if len(normalized) < 8:
            raise ValueError("请输入含国际区号的手机号，例如 +86138...")

        login_id = uuid.uuid4().hex[:12]
        tmp_base = self.session_dir / f"_tmp_{login_id}"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        unlink_session(tmp_base)
        client = TelegramClient(str(tmp_base), self.api_id, self.api_hash, proxy=self.proxy)
        await client.connect()
        try:
            await client.send_code_request(normalized)
        except Exception:
            await client.disconnect()
            unlink_session(tmp_base)
            raise

        async with self._lock:
            await self._expire_pending()
            self._pending[login_id] = PendingLogin(
                login_id=login_id,
                client=client,
                tmp_base=tmp_base,
                phone=normalized,
                group_id=group_id,
                force=force,
            )
        return LoginResult(
            done=False,
            step="code",
            login_id=login_id,
            message=f"验证码已发到 {normalized}",
        )

    async def submit_code(self, login_id: str, code: str) -> LoginResult:
        pending = self._require_pending(login_id)
        try:
            await pending.client.sign_in(pending.phone, code.strip())
        except SessionPasswordNeededError:
            return LoginResult(
                done=False,
                step="password",
                login_id=login_id,
                message="该账号开启了两步验证，请输入密码",
            )
        except (PhoneCodeInvalidError, PhoneCodeExpiredError) as error:
            raise ValueError(str(error) or "验证码无效或已过期") from error
        return await self._finalize(pending)

    async def submit_password(self, login_id: str, password: str) -> LoginResult:
        pending = self._require_pending(login_id)
        try:
            await pending.client.sign_in(password=password)
        except RPCError as error:
            raise ValueError(str(error) or "两步验证失败") from error
        return await self._finalize(pending)

    async def cancel(self, login_id: str) -> None:
        pending = self._pending.pop(login_id, None)
        if pending is None:
            return
        await self._discard(pending)

    def _require_pending(self, login_id: str) -> PendingLogin:
        pending = self._pending.get(login_id)
        if pending is None:
            raise ValueError("登录会话不存在或已过期，请重新开始")
        if time.monotonic() - pending.created_at > _PENDING_TTL_SECONDS:
            asyncio.create_task(self._discard(pending))
            self._pending.pop(login_id, None)
            raise ValueError("登录会话已超时，请重新开始")
        return pending

    async def _expire_pending(self) -> None:
        now = time.monotonic()
        stale = [key for key, item in self._pending.items() if now - item.created_at > _PENDING_TTL_SECONDS]
        for key in stale:
            pending = self._pending.pop(key, None)
            if pending:
                await self._discard(pending)

    async def _login_and_save(
        self,
        *,
        bot_token: str | None = None,
        group_id: int | None,
        force: bool,
    ) -> LoginResult:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        tmp_id = bot_token.split(":", 1)[0] if bot_token else uuid.uuid4().hex[:8]
        tmp_base = self.session_dir / f"_tmp_{tmp_id}"
        unlink_session(tmp_base)
        client = TelegramClient(str(tmp_base), self.api_id, self.api_hash, proxy=self.proxy)
        try:
            await client.start(bot_token=bot_token)
            return await self._save_connected(client, tmp_base, group_id, force)
        except Exception:
            await _safe_disconnect(client)
            unlink_session(tmp_base)
            raise

    async def _finalize(self, pending: PendingLogin) -> LoginResult:
        self._pending.pop(pending.login_id, None)
        try:
            return await self._save_connected(pending.client, pending.tmp_base, pending.group_id, pending.force)
        except Exception:
            await self._discard(pending)
            raise

    async def _save_connected(
        self,
        client: TelegramClient,
        tmp_base: Path,
        group_id: int | None,
        force: bool,
    ) -> LoginResult:
        me = await client.get_me()
        if me is None:
            raise RuntimeError("get_me() 返回空，登录未完成")
        is_bot = bool(getattr(me, "bot", False))
        username = getattr(me, "username", None)
        name = username or (f"bot_{me.id}" if is_bot else f"user_{me.id}")
        group_ok: bool | None = None
        group_error: str | None = None
        if group_id:
            try:
                await client.get_entity(group_id)
                group_ok = True
            except Exception as error:
                group_ok = False
                group_error = str(error)
                logger.warning("群组验证失败 chat_id=%s: %s", group_id, error)
        await _safe_disconnect(client)

        dest_base = self.session_dir / name
        dest_file = sqlite_path(dest_base)
        tmp_file = sqlite_path(tmp_base)
        existing = dest_file.exists() or any(
            path.stem.lower() == name.lower() for path in self.session_dir.glob("*.session")
        )
        if existing and not force:
            unlink_session(tmp_base)
            raise FileExistsError(f"sessions 已有 {name}.session，勾选覆盖后再试")
        if dest_file.exists():
            unlink_session(dest_base)
        if not tmp_file.exists():
            raise FileNotFoundError("临时 session 未生成")
        tmp_file.replace(dest_file)
        logger.info("已创建 session: %s", dest_file)
        return LoginResult(
            done=True,
            step="done",
            name=name,
            username=username,
            is_bot=is_bot,
            group_ok=group_ok,
            group_error=group_error,
            message=f"已保存 sessions/{name}.session，约 2 秒后自动加载为 Worker",
        )

    async def _discard(self, pending: PendingLogin) -> None:
        await _safe_disconnect(pending.client)
        unlink_session(pending.tmp_base)


async def _safe_disconnect(client: TelegramClient) -> None:
    try:
        await client.disconnect()
    except Exception:
        pass

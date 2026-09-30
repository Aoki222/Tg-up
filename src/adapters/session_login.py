"""控制台创建 session：落盘约定与 generate_session CLI 相同，交互改成 HTTP 多步。

为什么不在浏览器里跑 Telethon：
- api_id / api_hash 是进程身份，不能下发到前端。
- 用户登录要同一条 TelegramClient 上连续 send_code → sign_in → 两步密码。
- 生成的 sqlite session 必须落在本机 sessions/，现有 SessionPool 每 2 秒扫盘就会加载成 Worker。

结构：
- 临时文件 _tmp_<id>.session，登录成功再改名为 <username>.session，避免半成品被扫描进 Worker。
- Bot 一步完成；手机号分 start / code / password 三步。
- 二维码：qr_login() 拿到 tg://login 链接，浏览器自己画图。后台 wait 等手机确认，
  过期则 recreate。开了两步验证时改成 password 步。pending 在内存，超时清掉。
- 登录成功旁写 <name>.json，标明是 bot 还是用户，供选群时跳过 Bot。
- 绑定群组可选：登录后 get_entity，失败只警告，session 仍保存（和 CLI 一致）。
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import qrcode
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
    qr_url: str = ""
    qr_image: str = ""


@dataclass
class PendingLogin:
    login_id: str
    client: TelegramClient
    tmp_base: Path
    phone: str
    group_id: int | None
    force: bool
    kind: str = "phone"
    qr_login: object | None = None
    qr_url: str = ""
    needs_password: bool = False
    error: str | None = None
    result: LoginResult | None = None
    watcher: asyncio.Task | None = None
    created_at: float = field(default_factory=time.monotonic)


class SessionLoginService:
    """一次只推进一个 pending 用户登录；Bot 登录不占 pending。"""

    def __init__(self, session_dir: Path, api_id: int, api_hash: str, proxy_url: str | None = None):
        self.session_dir = session_dir
        self.api_id = api_id
        self.api_hash = api_hash
        self.proxy = parse_proxy(proxy_url)
        self._cleanup_orphan_qr_sessions()
        self._pending: dict[str, PendingLogin] = {}
        self._finished: dict[str, LoginResult] = {}
        self._lock = asyncio.Lock()

    def _cleanup_orphan_qr_sessions(self) -> None:
        """服务重启时删除上次未正常取消的二维码临时 session。"""
        if not self.session_dir.is_dir():
            return
        for path in self.session_dir.glob("_tmp_qr_*.session"):
            unlink_session(path)

    def list_saved(self) -> list[str]:
        if not self.session_dir.is_dir():
            return []
        names = []
        for path in sorted(self.session_dir.glob("*.session")):
            if path.stem.startswith("_tmp_"):
                continue
            names.append(path.stem)
        return names

    def list_accounts(self) -> list[dict]:
        """已保存的 session，区分 bot / 用户。没有旁路信息时按文件名前缀猜测。"""
        accounts = []
        for name in self.list_saved():
            meta_path = self.session_dir / f"{name}.json"
            is_bot: bool | None = None
            username = ""
            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    is_bot = bool(meta.get("is_bot"))
                    username = str(meta.get("username") or "")
                except (OSError, json.JSONDecodeError):
                    is_bot = None
            if is_bot is None:
                if name.startswith("bot_"):
                    is_bot = True
                elif name.startswith("user_"):
                    is_bot = False
            kind = "unknown" if is_bot is None else ("bot" if is_bot else "user")
            accounts.append({"name": name, "kind": kind, "username": username})
        return accounts

    async def start_bot(self, bot_token: str, group_id: int | None, force: bool) -> LoginResult:
        """校验 Bot Token，并把一次性 Bot 登录交给统一保存流程。"""
        token = bot_token.strip()
        if ":" not in token:
            raise ValueError("Bot Token 格式应为 <id>:<secret>")
        return await self._login_and_save(bot_token=token, group_id=group_id, force=force)

    async def start_user(self, phone: str, group_id: int | None, force: bool) -> LoginResult:
        """建立手机号登录客户端并发送验证码，后续由 submit_code 继续完成登录。"""
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

    async def start_qr(self, group_id: int | None, force: bool) -> LoginResult:
        """现场连接 Telegram、申请二维码，并把客户端交给后台 watcher 持续等待扫码。"""
        login_id = uuid.uuid4().hex[:12]
        tmp_base = self.session_dir / f"_tmp_qr_{login_id}"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        unlink_session(tmp_base)
        client = TelegramClient(str(tmp_base), self.api_id, self.api_hash, proxy=self.proxy)
        await client.connect()
        try:
            qr_login = await client.qr_login()
        except Exception:
            await _safe_disconnect(client)
            unlink_session(tmp_base)
            raise
        pending = PendingLogin(
            login_id=login_id,
            client=client,
            tmp_base=tmp_base,
            phone="",
            group_id=group_id,
            force=force,
            kind="qr",
            qr_login=qr_login,
            qr_url=qr_login.url,
        )
        async with self._lock:
            await self._expire_pending()
            self._pending[login_id] = pending
            pending.watcher = asyncio.create_task(self._watch_qr(pending))
        return self._qr_status(pending)

    def poll(self, login_id: str) -> LoginResult:
        """读取二维码登录状态；前端定时调用，结果来自内存中的 pending/finished。"""
        finished = self._finished.get(login_id)
        if finished is not None:
            return finished
        pending = self._require_pending(login_id)
        if pending.error:
            raise ValueError(pending.error)
        if pending.needs_password:
            return LoginResult(
                done=False,
                step="password",
                login_id=login_id,
                message="该账号开启了两步验证，请输入密码",
            )
        if pending.result is not None:
            return pending.result
        return self._qr_status(pending)

    async def _watch_qr(self, pending: PendingLogin) -> None:
        """后台等待 Telegram 确认扫码，过期时刷新二维码，成功后进入统一保存流程。"""
        qr_login = pending.qr_login
        while pending.login_id in self._pending and qr_login is not None:
            try:
                await qr_login.wait(timeout=8)
            except asyncio.TimeoutError:
                try:
                    await qr_login.recreate()
                    pending.qr_url = qr_login.url
                except Exception as error:
                    pending.error = str(error) or "二维码刷新失败"
                    return
                continue
            except SessionPasswordNeededError:
                pending.needs_password = True
                return
            except Exception as error:
                pending.error = str(error) or "二维码登录失败"
                return
            try:
                pending.result = await self._finalize(pending)
                self._finished[pending.login_id] = pending.result
            except Exception as error:
                pending.error = str(error) or "保存 session 失败"
            return

    def _qr_status(self, pending: PendingLogin) -> LoginResult:
        """把 Telethon 的二维码 URL 转成 API 响应，供前端绘制二维码。"""
        return LoginResult(
            done=False,
            step="qr",
            login_id=pending.login_id,
            message="用已登录的 Telegram 扫描二维码",
            qr_url=pending.qr_url,
            qr_image=_qr_png(pending.qr_url),
        )

    async def submit_code(self, login_id: str, code: str) -> LoginResult:
        """在原手机号客户端上提交验证码；需要 2FA 时把状态交给前端继续输入密码。"""
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
        """在同一 pending 客户端上提交 2FA 密码，成功后保存登录 session。"""
        pending = self._require_pending(login_id)
        try:
            await pending.client.sign_in(password=password)
        except RPCError as error:
            raise ValueError(str(error) or "两步验证失败") from error
        return await self._finalize(pending)

    async def cancel(self, login_id: str) -> None:
        """取消登录并释放客户端，同时删除本次二维码对应的临时 session。"""
        pending = self._pending.pop(login_id, None)
        if pending is None:
            # 即使后台 watcher 已经结束，也清理前端本次登录留下的文件。
            unlink_session(self.session_dir / f"_tmp_qr_{login_id}")
            return
        await self._discard(pending)

    def _require_pending(self, login_id: str) -> PendingLogin:
        """取出登录上下文并检查五分钟有效期，防止继续使用过期客户端。"""
        pending = self._pending.get(login_id)
        if pending is None:
            raise ValueError("登录会话不存在或已过期，请重新开始")
        if time.monotonic() - pending.created_at > _PENDING_TTL_SECONDS:
            asyncio.create_task(self._discard(pending))
            self._pending.pop(login_id, None)
            raise ValueError("登录会话已超时，请重新开始")
        return pending

    async def _expire_pending(self) -> None:
        """清理本次操作前发现的过期登录上下文，避免客户端和临时文件泄漏。"""
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
        """执行 Bot 的完整登录链路；失败时断开客户端并删除临时 session。"""
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
        """从 pending 移除登录上下文，并把已授权客户端交给保存函数。"""
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
        """读取账号身份、校验可选群组，断开客户端后将临时 session 原子改名保存。"""
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
        for _suffix in ("", "-wal", "-shm"):
            _src = Path(str(tmp_file) + _suffix)
            _dst = Path(str(dest_file) + _suffix)
            if _src.exists():
                _src.replace(_dst)
            elif _dst.exists():
                _dst.unlink()
        meta_path = self.session_dir / f"{name}.json"
        meta_path.write_text(
            json.dumps(
                {"is_bot": is_bot, "username": username or "", "user_id": int(me.id)},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
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
        """停止二维码 watcher、断开客户端，并删除未完成登录的临时 session。"""
        if pending.watcher is not None:
            pending.watcher.cancel()
        await _safe_disconnect(pending.client)
        unlink_session(pending.tmp_base)


def _qr_png(url: str) -> str:
    image = qrcode.make(url)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


async def _safe_disconnect(client: TelegramClient) -> None:
    try:
        await client.disconnect()
    except Exception:
        pass

#!/usr/bin/env python3
"""命令行生成 session，与控制台 SessionLoginService 共用落盘约定。

读取项目根 .env 的 API_ID / API_HASH（兼容 TELEGRAM_API_ID）。
批量读取 Bot Token，登录后按用户名生成 sessions/<username>.session。

.env 批量 Token（任选一种，可混用；同名 key 重复多行也可以）：
  TELEGRAM_BOT_TOKEN=123:AAA
  TELEGRAM_BOT_TOKEN=456:BBB
  TELEGRAM_BOT_TOKEN=123:AAA,456:BBB
  TELEGRAM_BOT_TOKEN_1=123:AAA
  TELEGRAM_BOT_TOKEN_2=456:BBB

sessions/ 里已有同名 .session 则跳过，不重复生成；--force 才覆盖。

用法:
  python -m src.adapters.generate_session
  python -m src.adapters.generate_session --force
  python -m src.adapters.generate_session --bot-token 123:AAA --bot-token 456:BBB
  python -m src.adapters.generate_session --mode user
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import re
import sys
from pathlib import Path

from .session_login import parse_proxy, sqlite_path

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
SESSION_DIR = PROJECT_DIR / "sessions"
ENV_PATH = PROJECT_DIR / ".env"

try:
    from dotenv import load_dotenv

    load_dotenv(ENV_PATH)
except ImportError:
    pass

try:
    from telethon import TelegramClient
    from telethon.errors import RPCError
except ImportError:
    print("[错误] 未安装 telethon，请先执行: pip install telethon")
    sys.exit(1)


TOKEN_SPLIT = re.compile(r"[\s,;]+")
TOKEN_SHAPE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")


def env_str(*keys: str) -> str:
    for key in keys:
        val = os.getenv(key, "")
        if val is None:
            continue
        val = str(val).strip().strip('"').strip("'")
        if val:
            return val
    return ""


def prompt(text: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{text}{suffix}: ").strip().strip('"').strip("'")
    return val if val else default


def split_tokens(raw: str) -> list[str]:
    parts = TOKEN_SPLIT.split(raw.strip().strip('"').strip("'"))
    out: list[str] = []
    for part in parts:
        token = part.strip().strip('"').strip("'")
        if token:
            out.append(token)
    return out


def _is_token_key(key: str) -> bool:
    return key == "TELEGRAM_BOT_TOKENS" or key.startswith("TELEGRAM_BOT_TOKEN")


def parse_env_file_tokens(path: Path) -> list[str]:
    """按行读取 .env，重复的 TELEGRAM_BOT_TOKEN 全部保留（不走 os.environ 覆盖）。"""
    found: list[str] = []
    if not path.is_file():
        return found
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return found
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        if not _is_token_key(key.strip()):
            continue
        if " #" in val:
            val = val.split(" #", 1)[0]
        found.extend(split_tokens(val))
    return found


def collect_bot_tokens(cli_tokens: list[str] | None) -> list[str]:
    found: list[str] = []
    for raw in cli_tokens or []:
        found.extend(split_tokens(raw))
    found.extend(parse_env_file_tokens(ENV_PATH))
    for key, val in os.environ.items():
        if _is_token_key(key) and val:
            found.extend(split_tokens(val))

    unique: list[str] = []
    seen: set[str] = set()
    for token in found:
        if token in seen:
            continue
        seen.add(token)
        unique.append(token)
    return unique


def list_session_names(session_dir: Path) -> set[str]:
    if not session_dir.is_dir():
        return set()
    names: set[str] = set()
    for path in session_dir.iterdir():
        if not path.is_file() or path.suffix != ".session":
            continue
        if path.stem.startswith("_tmp_"):
            continue
        names.add(path.stem)
    return names


def session_exists(session_dir: Path, name: str) -> bool:
    if sqlite_path(session_dir / name).exists():
        return True
    lower = name.lower()
    return any(existing.lower() == lower for existing in list_session_names(session_dir))


def fetch_bot_username(token: str, proxy_url: str | None) -> str | None:
    """用 Bot API getMe 解析用户名，便于生成前按 sessions/<name>.session 去重。"""
    import json
    from urllib.parse import urlparse
    from urllib.request import ProxyHandler, Request, build_opener, urlopen

    host = "api.telegram.org"
    path = f"/bot{token}/getMe"
    timeout = 20

    def parse_username(payload: str) -> str | None:
        data = json.loads(payload)
        if not data.get("ok"):
            print(f"[警告] {token_hint(token)} getMe: {data.get('description', '失败')}")
            return None
        return data.get("result", {}).get("username") or None

    try:
        scheme = urlparse(proxy_url).scheme.lower() if proxy_url else ""
        url = f"https://{host}{path}"
        if scheme in ("http", "https"):
            opener = build_opener(ProxyHandler({"http": proxy_url, "https": proxy_url}))
            with opener.open(Request(url), timeout=timeout) as resp:
                return parse_username(resp.read().decode("utf-8", errors="replace"))
        with urlopen(Request(url), timeout=timeout) as resp:
            return parse_username(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        print(f"[警告] {token_hint(token)} 预检查失败: {e}，改为登录后判断")
        return None


def validate_token(token: str) -> bool:
    if TOKEN_SHAPE.match(token):
        return True
    print(f"[警告] Token 格式异常，已跳过: {token[:8]}...")
    return False


def resolve_api(args) -> tuple[int, str]:
    api_id = args.api_id or env_str("API_ID", "TELEGRAM_API_ID") or prompt("请输入 API_ID")
    api_hash = args.api_hash or env_str("API_HASH", "TELEGRAM_API_HASH") or prompt("请输入 API_HASH")
    try:
        api_id_int = int(str(api_id).strip())
        if api_id_int == 0:
            raise ValueError
    except ValueError:
        print("[错误] API_ID 必须为非 0 整数")
        sys.exit(1)
    api_hash = api_hash.strip().strip('"').strip("'")
    if len(api_hash) < 10:
        print("[错误] API_HASH 长度异常")
        sys.exit(1)
    return api_id_int, api_hash


def resolve_group_id(args) -> int | None:
    raw = args.group_id if args.group_id is not None else env_str("TELEGRAM_GROUP_ID")
    raw = str(raw).strip() if raw else ""
    if not raw or raw == "0":
        return None
    try:
        gid = int(raw)
        return None if gid == 0 else gid
    except ValueError:
        print(f"[错误] TELEGRAM_GROUP_ID 必须是整数，当前值: {raw}")
        sys.exit(1)


def resolve_session_dir(args) -> Path:
    raw = args.session_dir or args.session or env_str("TELEGRAM_SESSION_DIR", "SESSION_DIR")
    if not raw:
        return SESSION_DIR
    path = Path(raw)
    if path.suffix == ".session":
        return path.parent if str(path.parent) else Path(".")
    return path


def unlink_session(base: Path) -> None:
    target = sqlite_path(base)
    for path in (target, Path(str(target) + "-journal"), Path(str(target) + "-wal"), Path(str(target) + "-shm")):
        if path.exists() and path.is_file():
            path.unlink()
            print(f"[提示] 已删除: {path}")


def token_hint(token: str) -> str:
    left, _, _ = token.partition(":")
    return f"{left}:***"


def ask_phone() -> str:
    phone = prompt("请输入手机号（含区号，如 +8613800138000）").replace(" ", "").replace("-", "")
    if phone and not phone.startswith("+"):
        phone = "+" + phone
    return phone


def ask_tokens_interactive() -> list[str]:
    print("请输入 Bot Token（每行一个，空行结束）:")
    tokens: list[str] = []
    while True:
        line = input("  token> ").strip().strip('"').strip("'")
        if not line:
            break
        tokens.extend(split_tokens(line))
    return tokens


async def check_group(client: TelegramClient, group_id: int) -> None:
    print(f"[*] 验证群组: {group_id}")
    try:
        entity = await client.get_entity(group_id)
        title = getattr(entity, "title", group_id)
        print(f"[信息] 群组: {title} (ID: {getattr(entity, 'id', group_id)})")
    except (RPCError, ValueError, Exception) as e:
        print(f"[警告] 群组验证失败: {e}")


async def login_and_save(
    *,
    api_id: int,
    api_hash: str,
    proxy,
    session_dir: Path,
    force: bool,
    group_id: int | None,
    bot_token: str | None = None,
) -> tuple[str, Path | None]:
    """登录一个账号，按 get_me().username 落盘 session。返回 (name, path)。"""
    session_dir.mkdir(parents=True, exist_ok=True)
    tmp_id = (bot_token.split(":", 1)[0] if bot_token else "user")
    tmp_base = session_dir / f"_tmp_{tmp_id}"
    unlink_session(tmp_base)

    client = TelegramClient(str(tmp_base), api_id, api_hash, proxy=proxy)
    try:
        try:
            if bot_token:
                await client.start(bot_token=bot_token)
            else:
                await client.start(
                    phone=ask_phone,
                    code_callback=lambda: prompt("请输入验证码"),
                    password=lambda: getpass.getpass("两步验证密码: "),
                )
            me = await client.get_me()
            if me is None:
                raise RuntimeError("get_me() 返回空，登录未完成")
            is_bot = bool(getattr(me, "bot", False))
            name = me.username or (f"bot_{me.id}" if is_bot else f"user_{me.id}")
            print(f"[成功] {'Bot' if is_bot else '用户'} 登录: @{name} (ID: {me.id})")
            if group_id:
                await check_group(client, group_id)
        finally:
            await client.disconnect()

        dest_base = session_dir / name
        dest_file = sqlite_path(dest_base)
        tmp_file = sqlite_path(tmp_base)

        if (not force) and session_exists(session_dir, name):
            unlink_session(tmp_base)
            print(f"[跳过] sessions 已有 {name}.session，不重复生成")
            return name, None

        if dest_file.exists():
            unlink_session(dest_base)
        if not tmp_file.exists():
            raise FileNotFoundError(f"临时 session 未生成: {tmp_file}")
        tmp_file.replace(dest_file)
        print(f"[成功] Session: {dest_file.resolve()} ({dest_file.stat().st_size} bytes)")
        return name, dest_file
    except Exception:
        unlink_session(tmp_base)
        raise


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="批量生成以 Bot 用户名命名的 Telegram session",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m src.adapters.generate_session
  python -m src.adapters.generate_session --force
  python -m src.adapters.generate_session --bot-token 123:AAA --bot-token 456:BBB
  python -m src.adapters.generate_session --mode user --session-dir ./sessions
        """,
    )
    p.add_argument("--api-id", dest="api_id", help="默认读取 .env API_ID")
    p.add_argument("--api-hash", dest="api_hash", help="默认读取 .env API_HASH")
    p.add_argument("--bot-token", dest="bot_token", action="append", help="可重复传入，多个 Token 批量生成")
    p.add_argument("--session-dir", dest="session_dir", help="Session 输出目录，默认 ./sessions")
    p.add_argument("--session", dest="session", help="兼容旧参数，视为输出目录")
    p.add_argument("--group-id", dest="group_id", help="可选，登录后验证群组访问")
    p.add_argument("--mode", choices=["auto", "user", "bot"], default="auto", help="auto: 有 Token 则批量 Bot，否则用户登录")
    p.add_argument("--proxy", dest="proxy", help="代理 URL，默认读取 TELEGRAM_PROXY")
    p.add_argument("--force", action="store_true", help="覆盖已存在的同名 session")
    return p


async def async_main() -> int:
    args = build_parser().parse_args()
    api_id, api_hash = resolve_api(args)
    proxy_url = args.proxy or env_str("TELEGRAM_PROXY")
    proxy = parse_proxy(proxy_url) if proxy_url else None
    if proxy_url and not proxy:
        print("[警告] 代理配置无效，将使用直连")

    group_id = resolve_group_id(args)
    session_dir = resolve_session_dir(args)
    tokens = [t for t in collect_bot_tokens(args.bot_token) if validate_token(t)]
    existing = list_session_names(session_dir)
    mode = args.mode
    if mode == "auto":
        mode = "bot" if tokens else ("user" if not sys.stdin.isatty() else "")
        if not mode:
            choice = prompt("未检测到 Bot Token。[1] 输入 Token  [2] 手机号用户", "1")
            mode = "user" if choice.strip() in ("2", "user", "n") else "bot"

    print("=" * 60)
    print(f" API ID    : {api_id}")
    print(f" API Hash  : {api_hash[:4]}****{api_hash[-4:]}")
    print(f" Session目录: {session_dir.resolve()}")
    print(f" 已有 session: {', '.join(sorted(existing)) if existing else '无'}")
    print(f" 模式      : {mode}")
    print(f" Token 数量 : {len(tokens) if mode == 'bot' else '-'}")
    print(f" 目标群组  : {group_id if group_id else '未指定'}")
    print(f" 代理      : {proxy if proxy else '直连'}")
    print("=" * 60)

    ok: list[Path] = []
    skipped = 0
    failed = 0

    common = dict(
        api_id=api_id,
        api_hash=api_hash,
        proxy=proxy,
        session_dir=session_dir,
        force=args.force,
        group_id=group_id,
    )

    if mode == "user":
        try:
            name, path = await login_and_save(**common)
            if path:
                ok.append(path)
            else:
                skipped += 1
        except Exception as e:
            failed += 1
            print(f"[失败] 用户登录: {type(e).__name__}: {e}")
    else:
        if not tokens:
            tokens = [t for t in ask_tokens_interactive() if validate_token(t)]
        if not tokens:
            print("[错误] Bot 模式需要至少一个 Bot Token")
            return 1
        for i, token in enumerate(tokens, 1):
            print(f"\n--- ({i}/{len(tokens)}) {token_hint(token)} ---")
            if not args.force:
                username = fetch_bot_username(token, proxy_url)
                if username and session_exists(session_dir, username):
                    print(f"[跳过] sessions 已有 {username}.session，不重复生成")
                    skipped += 1
                    continue
            try:
                name, path = await login_and_save(**common, bot_token=token)
                if path:
                    ok.append(path)
                    existing.add(name)
                    print(f"[完成] @{name}")
                else:
                    skipped += 1
            except Exception as e:
                failed += 1
                print(f"[失败] {token_hint(token)}: {type(e).__name__}: {e}")

    print("\n" + "=" * 60)
    print(f" 成功 {len(ok)}  跳过 {skipped}  失败 {failed}")
    for path in ok:
        print(f"  - {path}")
    print("=" * 60)
    return 1 if failed else 0


def main() -> None:
    try:
        sys.exit(asyncio.run(async_main()))
    except KeyboardInterrupt:
        print("\n[退出] 用户取消")
        sys.exit(0)


if __name__ == "__main__":
    main()

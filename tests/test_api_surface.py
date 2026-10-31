"""未知接口返回 JSON；进度流用短时票据，不接受查询参数里的长期令牌。"""

import json

from src.adapters.progress import ProgressHub
from src.api.app import create_api
from src.api.present import telegram_message_url
from src.api.stream_ticket import StreamTickets


def test_telegram_message_url_uses_the_public_channel_id() -> None:
    assert telegram_message_url("-1001234567890", "42") == "https://t.me/c/1234567890/42"
    assert telegram_message_url("123", "42") is None
    assert telegram_message_url("-1001234567890", None) is None


def test_stream_ticket_expires() -> None:
    tickets = StreamTickets(ttl_seconds=600)
    token, ttl = tickets.issue()
    assert ttl == 600
    assert tickets.valid(token)
    assert not tickets.valid(None)
    assert not tickets.valid("other")
    tickets._expires[token] = 0
    assert not tickets.valid(token)


async def _call(app, method: str, path: str, query: bytes = b"") -> tuple[int, dict | None]:
    body = bytearray()
    status = {"code": 500}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            status["code"] = message["status"]
        elif message["type"] == "http.response.body":
            body.extend(message.get("body") or b"")

    await app(
        {
            "type": "http",
            "asgi": {"spec_version": "2.3", "version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query,
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("test", 80),
        },
        receive,
        send,
    )
    if not body:
        return status["code"], None
    return status["code"], json.loads(body.decode())


async def test_unknown_api_without_login_is_401() -> None:
    app = create_api(ProgressHub())
    status, payload = await _call(app, "GET", "/api/does-not-exist")
    assert status == 401
    assert payload == {"detail": "需要登录"}


async def test_tasks_and_stream_require_login() -> None:
    app = create_api(ProgressHub())
    status, payload = await _call(app, "GET", "/api/tasks")
    assert status == 401
    assert payload == {"detail": "需要登录"}
    status, payload = await _call(app, "GET", "/api/progress/stream")
    assert status == 401
    assert payload == {"detail": "需要登录"}

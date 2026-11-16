"""未知接口返回 JSON；进度流用短时票据，不接受查询参数里的长期令牌。"""

import json

from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

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


def _gzip_app():
    app = create_api(ProgressHub())

    async def probe(_request):
        return JSONResponse({"blob": "z" * 800})

    async def stream(_request):
        async def chunks():
            yield b"data: hi\n\n"

        return StreamingResponse(chunks(), media_type="text/event-stream")

    app.router.routes.insert(0, Route("/gzip-probe", probe, methods=["GET"]))
    app.router.routes.insert(0, Route("/gzip-stream", stream, methods=["GET"]))
    return app


async def _raw(app, path: str, accept_encoding: str | None = None) -> tuple[int, dict[str, str], bytes]:
    body = bytearray()
    status = {"code": 500}
    headers: dict[str, str] = {}
    requested = False

    async def receive():
        nonlocal requested
        if requested:
            return {"type": "http.disconnect"}
        requested = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            status["code"] = message["status"]
            for key, value in message.get("headers") or []:
                headers[key.decode().lower()] = value.decode()
        elif message["type"] == "http.response.body":
            body.extend(message.get("body") or b"")

    raw_headers = []
    if accept_encoding is not None:
        raw_headers.append((b"accept-encoding", accept_encoding.encode()))
    await app(
        {
            "type": "http",
            "asgi": {"spec_version": "2.3", "version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": raw_headers,
            "client": ("127.0.0.1", 12345),
            "server": ("test", 80),
        },
        receive,
        send,
    )
    return status["code"], headers, bytes(body)


async def test_gzip_compresses_json_and_leaves_the_progress_stream() -> None:
    app = _gzip_app()
    gzip_mw = [item for item in app.user_middleware if item.cls is GZipMiddleware]
    assert len(gzip_mw) == 1
    assert gzip_mw[0].kwargs.get("compresslevel") == 5

    status, headers, body = await _raw(app, "/gzip-probe", "gzip")
    assert status == 200
    assert headers.get("content-encoding") == "gzip"
    assert body[:2] == b"\x1f\x8b"

    status, headers, body = await _raw(app, "/api/health", "gzip")
    assert status == 200
    assert "content-encoding" not in headers
    assert json.loads(body) == {"ok": True}

    status, headers, body = await _raw(app, "/gzip-stream", "gzip")
    assert status == 200
    assert "content-encoding" not in headers
    assert body.startswith(b"data:")


async def test_tasks_and_stream_require_login() -> None:
    app = create_api(ProgressHub())
    status, payload = await _call(app, "GET", "/api/tasks")
    assert status == 401
    assert payload == {"detail": "需要登录"}
    status, payload = await _call(app, "GET", "/api/progress/stream")
    assert status == 401
    assert payload == {"detail": "需要登录"}

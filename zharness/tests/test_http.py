from types import SimpleNamespace

import pytest
from starlette.types import Message, Receive, Scope, Send
from zharness.server import http as http_module
from zharness.server.http import ThreadSandboxCleanupMiddleware, lifespan


class FakeManager:
    def __init__(self) -> None:
        self.removed: list[str] = []

    def remove_for_thread(self, thread_id: str) -> bool:
        self.removed.append(thread_id)
        return True

    def stop_all(self) -> list[str]:
        return ["container-one", "container-two"]


def _scope(*, method: str = "DELETE", path: str = "/threads/thread-one") -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "scheme": "http",
        "method": method,
        "root_path": "",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "state": {},
    }


async def _receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


@pytest.mark.asyncio
async def test_successful_thread_delete_removes_sandbox(monkeypatch) -> None:
    manager = FakeManager()
    monkeypatch.setattr(http_module, "get_sandbox_manager", lambda: manager)

    async def run_immediately(function, *args):
        return function(*args)

    monkeypatch.setattr(http_module.asyncio, "to_thread", run_immediately)

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = ThreadSandboxCleanupMiddleware(app)
    messages: list[Message] = []

    async def send(message: Message) -> None:
        messages.append(message)

    await middleware(_scope(), _receive, send)

    assert manager.removed == ["thread-one"]
    assert messages[0]["status"] == 204


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "status"),
    [
        (_scope(method="GET"), 204),
        (_scope(path="/threads/thread-one/state"), 204),
        (_scope(), 404),
        ({"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}, None),
    ],
)
async def test_other_requests_do_not_remove_sandbox(
    scope: Scope, status: int | None, monkeypatch
) -> None:
    manager = FakeManager()
    monkeypatch.setattr(http_module, "get_sandbox_manager", lambda: manager)
    called = SimpleNamespace(value=False)

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        called.value = True
        if status is not None:
            await send({"type": "http.response.start", "status": status, "headers": []})
            await send({"type": "http.response.body", "body": b""})

    async def send(message: Message) -> None:
        pass

    middleware = ThreadSandboxCleanupMiddleware(app)
    await middleware(scope, _receive, send)

    assert called.value is True
    assert manager.removed == []


@pytest.mark.asyncio
async def test_lifespan_stops_all_sandboxes(monkeypatch) -> None:
    manager = FakeManager()
    calls: list[str] = []
    monkeypatch.setattr(http_module, "get_sandbox_manager", lambda: manager)

    async def run_immediately(function, *args):
        calls.append("stop")
        return function(*args)

    monkeypatch.setattr(http_module.asyncio, "to_thread", run_immediately)

    async with lifespan(http_module.app):
        assert calls == []

    assert calls == ["stop"]

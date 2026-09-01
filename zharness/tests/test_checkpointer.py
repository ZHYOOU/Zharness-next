from contextlib import asynccontextmanager

import pytest
from zharness.server import checkpointer as checkpointer_module


@pytest.mark.asyncio
async def test_create_postgres_checkpointer_initializes_and_closes(
    monkeypatch,
) -> None:
    events: list[object] = []

    class FakeCheckpointer:
        async def setup(self) -> None:
            events.append("setup")

    fake_checkpointer = FakeCheckpointer()

    @asynccontextmanager
    async def open_checkpointer(postgres_uri: str, *, serde):
        events.append(("open", postgres_uri, serde))
        yield fake_checkpointer
        events.append("close")

    class FakePostgresSaver:
        from_conn_string = staticmethod(open_checkpointer)

    monkeypatch.setenv(
        "ZHARNESS_POSTGRES_URI",
        "postgresql://user:password@localhost:5432/zharness",
    )
    monkeypatch.setattr(
        checkpointer_module,
        "AsyncPostgresSaver",
        FakePostgresSaver,
    )

    async with checkpointer_module.create_postgres_checkpointer() as checkpointer:
        assert checkpointer is fake_checkpointer
        assert events == [
            (
                "open",
                "postgresql://user:password@localhost:5432/zharness",
                checkpointer_module._STRICT_SERIALIZER,
            ),
            "setup",
        ]

    assert events[-1] == "close"


def test_checkpoint_serializer_is_strict_without_environment(monkeypatch) -> None:
    monkeypatch.delenv("LANGGRAPH_STRICT_MSGPACK", raising=False)

    assert checkpointer_module._STRICT_SERIALIZER._allowed_msgpack_modules is None


@pytest.mark.asyncio
async def test_create_postgres_checkpointer_requires_uri(monkeypatch) -> None:
    monkeypatch.delenv("ZHARNESS_POSTGRES_URI", raising=False)
    monkeypatch.setenv("ZHARNESS_POSTGRES_MANAGED", "false")

    with pytest.raises(RuntimeError, match="ZHARNESS_POSTGRES_URI is required"):
        async with checkpointer_module.create_postgres_checkpointer():
            pass


def test_postgres_uri_uses_managed_compose_settings(monkeypatch) -> None:
    monkeypatch.delenv("ZHARNESS_POSTGRES_URI", raising=False)
    monkeypatch.setenv("ZHARNESS_POSTGRES_MANAGED", "true")
    monkeypatch.setenv("ZHARNESS_POSTGRES_USER", "agent user")
    monkeypatch.setenv("ZHARNESS_POSTGRES_PASSWORD", "secret/password")
    monkeypatch.setenv("ZHARNESS_POSTGRES_DB", "agent db")
    monkeypatch.setenv("ZHARNESS_POSTGRES_PORT", "55432")

    assert checkpointer_module._postgres_uri() == (
        "postgresql://agent%20user:secret%2Fpassword@127.0.0.1:55432/agent%20db"
    )

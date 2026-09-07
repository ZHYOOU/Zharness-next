"""Tests for the memory middleware hooks. / 记忆中间件钩子的测试。"""

from __future__ import annotations

import asyncio
import contextvars

import pytest
from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from zharness.memory import middleware as middleware_module
from zharness.memory import service as service_module
from zharness.memory.middleware import MemoryMiddleware, drain_memory_tasks
from zharness.memory.types import Fact, MemoryProfile, utcnow

_EMPTY_EXTRACTION = '{"facts": [], "removals": [], "profile": null}'


def _fake_model() -> FakeMessagesListChatModel:
    return FakeMessagesListChatModel(responses=[AIMessage(content=_EMPTY_EXTRACTION)])


class _FakeService:
    def __init__(self) -> None:
        self.top_facts_limit = 0
        self.injected_facts: list[Fact] = []
        self.injected_profile: MemoryProfile | None = None
        self.extraction_calls: list[str] = []

    async def top_facts(self, limit, min_confidence=None):
        self.top_facts_limit = limit
        now = utcnow()
        return [
            Fact(
                id="f1",
                content="Prefers Python",
                confidence=0.9,
                created_at=now,
                updated_at=now,
            )
        ]

    async def get_profile(self):
        return MemoryProfile(user_id="alice", work_context="Backend engineer")

    async def apply_extraction(self, result, *, thread_id=None):
        self.extraction_calls.append(thread_id or "none")
        return {"added": 0}

    async def close(self):
        pass


def _handler(request):
    return request


async def _async_handler(request):
    return request


def _middleware(**kwargs) -> MemoryMiddleware:
    defaults = {
        "model": _fake_model(),
        "extraction_enabled": True,
        "injection_enabled": True,
        "user_id": "alice",
    }
    defaults.update(kwargs)
    return MemoryMiddleware(**defaults)


def _request_with_system(system_message=None):
    from langchain.agents.middleware.types import ModelRequest

    return ModelRequest(
        model=_DUMMY_MODEL,
        messages=[HumanMessage(content="test")],
        system_message=system_message,
    )


class _DummyModel:
    pass


_DUMMY_MODEL = _DummyModel()


@pytest.mark.asyncio
async def test_middleware_registers_memory_tools() -> None:
    middleware = _middleware()
    names = {tool.name for tool in middleware.tools}
    assert names == {
        "memory_search",
        "memory_add",
        "memory_update",
        "memory_delete",
    }


@pytest.mark.asyncio
async def test_awrap_model_call_injects_memory_block(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(middleware_module, "get_memory_service", lambda: service)
    middleware = _middleware()

    response = await middleware.awrap_model_call(
        _request_with_system(),
        _async_handler,
    )

    text = response.system_message.text
    assert "<memory>" in text
    assert "Prefers Python" in text
    assert "Work: Backend engineer" in text


@pytest.mark.asyncio
async def test_awrap_model_call_appends_to_existing_system_message(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(middleware_module, "get_memory_service", lambda: service)
    middleware = _middleware()

    response = await middleware.awrap_model_call(
        _request_with_system(SystemMessage(content="You are a coding agent.")),
        _async_handler,
    )

    text = response.system_message.text
    assert text.startswith("You are a coding agent.")
    assert "<memory>" in text


@pytest.mark.asyncio
async def test_awrap_model_call_skips_when_injection_disabled(monkeypatch) -> None:
    monkeypatch.setattr(middleware_module, "get_memory_service", lambda: _FakeService())
    middleware = _middleware(injection_enabled=False)

    response = await middleware.awrap_model_call(_request_with_system(), _async_handler)

    assert response.system_message is None


@pytest.mark.asyncio
async def test_awrap_model_call_degrades_on_unavailable_service(monkeypatch) -> None:
    def unavailable():
        raise service_module.MemoryUnavailableError("db down")

    monkeypatch.setattr(middleware_module, "get_memory_service", unavailable)
    middleware = _middleware()

    response = await middleware.awrap_model_call(_request_with_system(), _async_handler)

    assert response.system_message is None


@pytest.mark.asyncio
async def test_wrap_model_call_passes_through_synchronously() -> None:
    middleware = _middleware()
    response = middleware.wrap_model_call(_request_with_system(), _handler)
    assert response.system_message is None


@pytest.mark.asyncio
async def test_aafter_agent_extracts_new_messages(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(middleware_module, "get_memory_service", lambda: service)
    middleware = _middleware()
    messages = [
        HumanMessage(content="I prefer Python.", id="m1"),
        AIMessage(content="Noted.", id="m2"),
    ]

    result = await middleware.aafter_agent({"messages": messages}, _runtime("thread-1"))

    assert result is None
    await drain_memory_tasks()
    assert service.extraction_calls == ["thread-1"]
    assert middleware._watermarks["thread-1"] == "m2"


@pytest.mark.asyncio
async def test_aafter_agent_skips_repeated_extraction(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(middleware_module, "get_memory_service", lambda: service)
    middleware = _middleware()
    messages = [HumanMessage(content="Hello", id="m1")]

    await middleware.aafter_agent({"messages": messages}, _runtime("thread-1"))
    await middleware.aafter_agent({"messages": messages}, _runtime("thread-1"))

    await drain_memory_tasks()
    assert len(service.extraction_calls) == 1


@pytest.mark.asyncio
async def test_aafter_agent_does_not_advance_watermark_on_failure(monkeypatch) -> None:
    class FailingService(_FakeService):
        async def top_facts(self, limit, min_confidence=None):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        middleware_module, "get_memory_service", lambda: FailingService()
    )
    middleware = _middleware()
    messages = [HumanMessage(content="Hello", id="m1")]

    await middleware.aafter_agent({"messages": messages}, _runtime("thread-1"))

    await drain_memory_tasks()
    assert "thread-1" not in middleware._watermarks


@pytest.mark.asyncio
async def test_aafter_agent_skips_when_disabled(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(middleware_module, "get_memory_service", lambda: service)
    middleware = _middleware(extraction_enabled=False)

    await middleware.aafter_agent(
        {"messages": [HumanMessage(content="Hi", id="m1")]},
        _runtime("thread-1"),
    )

    assert service.extraction_calls == []


@pytest.mark.asyncio
async def test_after_agent_sync_passes_through() -> None:
    middleware = _middleware()
    assert middleware.after_agent({"messages": []}, None) is None


def test_new_messages_excludes_tool_results() -> None:
    middleware = _middleware()
    messages = [
        HumanMessage(content="run the checks", id="m1"),
        ToolMessage(content="ok", tool_call_id="c1", id="t1"),
        ToolMessage(content="done", tool_call_id="c2", id="t2"),
        ToolMessage(content="failed", tool_call_id="c3", id="t3"),
        AIMessage(content="All done.", id="m2"),
    ]
    new = middleware._new_messages("thread-1", messages)

    assert [type(m).__name__ for m in new] == ["HumanMessage", "AIMessage"]
    assert new == [messages[0], messages[-1]]


@pytest.mark.asyncio
async def test_aafter_agent_warns_once_on_missing_config(monkeypatch, caplog) -> None:
    def unconfigured():
        raise RuntimeError("ZHARNESS_POSTGRES_URI is required")

    monkeypatch.setattr(middleware_module, "get_memory_service", unconfigured)
    middleware = _middleware()
    messages = [HumanMessage(content="Hello", id="m1")]

    await middleware.aafter_agent({"messages": messages}, _runtime("thread-1"))
    await middleware.aafter_agent({"messages": messages}, _runtime("thread-1"))

    await drain_memory_tasks()
    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "PostgreSQL is not configured" in warnings[0].message


def _runtime(thread_id: str):
    from types import SimpleNamespace

    return SimpleNamespace(execution_info=SimpleNamespace(thread_id=thread_id))


@pytest.mark.asyncio
async def test_background_extraction_does_not_block_and_detaches_context(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    marker = contextvars.ContextVar("test_run_context", default=None)
    observed = []

    class SlowService(_FakeService):
        async def apply_extraction(self, result, *, thread_id=None):
            observed.append(marker.get())
            started.set()
            await release.wait()
            return await super().apply_extraction(result, thread_id=thread_id)

    service = SlowService()
    monkeypatch.setattr(middleware_module, "get_memory_service", lambda: service)
    middleware = _middleware()
    token = marker.set("parent-stream")
    try:
        await asyncio.wait_for(
            middleware.aafter_agent(
                {"messages": [HumanMessage(content="Hello", id="m1")]}, _runtime("t")
            ),
            timeout=1,
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        assert service.extraction_calls == []
        assert observed == [None]
        assert "t" not in middleware._watermarks
    finally:
        marker.reset(token)
        release.set()
        await drain_memory_tasks()
    assert middleware._watermarks["t"] == "m1"


@pytest.mark.asyncio
async def test_queued_turns_are_serialized_and_deduplicated(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()

    class SlowService(_FakeService):
        async def apply_extraction(self, result, *, thread_id=None):
            started.set()
            await release.wait()
            return await super().apply_extraction(result, thread_id=thread_id)

    service = SlowService()
    monkeypatch.setattr(middleware_module, "get_memory_service", lambda: service)
    middleware = _middleware()
    first = HumanMessage(content="Hello", id="m1")
    second = HumanMessage(content="I like Python", id="m2")
    await middleware.aafter_agent({"messages": [first]}, _runtime("t"))
    await asyncio.wait_for(started.wait(), timeout=1)
    for _ in range(3):
        await middleware.aafter_agent({"messages": [first, second]}, _runtime("t"))
    assert len(middleware._workers) == 1
    release.set()
    await drain_memory_tasks()
    assert service.extraction_calls == ["t", "t"]
    assert middleware._watermarks["t"] == "m2"


@pytest.mark.asyncio
async def test_shutdown_cancels_unfinished_extraction(monkeypatch):
    started = asyncio.Event()

    class SlowService(_FakeService):
        async def top_facts(self, *args, **kwargs):
            started.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(middleware_module, "get_memory_service", lambda: SlowService())
    middleware = _middleware()
    await middleware.aafter_agent(
        {"messages": [HumanMessage(content="Hello", id="m1")]}, _runtime("t")
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    await drain_memory_tasks(timeout=0)
    assert not middleware._workers
    assert not middleware._watermarks


@pytest.mark.asyncio
async def test_invalid_extraction_retries_on_next_turn(monkeypatch):
    service = _FakeService()
    monkeypatch.setattr(middleware_module, "get_memory_service", lambda: service)
    middleware = _middleware(
        model=FakeMessagesListChatModel(
            responses=[
                AIMessage(content="Already remembered!"),
                AIMessage(content=_EMPTY_EXTRACTION),
            ]
        )
    )
    state = {"messages": [HumanMessage(content="Hello", id="m1")]}
    await middleware.aafter_agent(state, _runtime("t"))
    await drain_memory_tasks()
    assert not middleware._watermarks
    await middleware.aafter_agent(state, _runtime("t"))
    await drain_memory_tasks()
    assert middleware._watermarks["t"] == "m1"


@pytest.mark.asyncio
async def test_graph_stream_finishes_before_background_model(monkeypatch):
    from langgraph.graph import END, START, StateGraph

    release = asyncio.Event()

    class SlowModel(FakeMessagesListChatModel):
        async def ainvoke(self, *args, **kwargs):
            await release.wait()
            return await super().ainvoke(*args, **kwargs)

    service = _FakeService()
    monkeypatch.setattr(middleware_module, "get_memory_service", lambda: service)
    middleware = _middleware(
        model=SlowModel(responses=[AIMessage(content=_EMPTY_EXTRACTION)])
    )

    async def finish(state):
        await middleware.aafter_agent(state, _runtime("t"))
        return state

    builder = StateGraph(dict)
    builder.add_node("finish", finish)
    builder.add_edge(START, "finish")
    builder.add_edge("finish", END)
    graph = builder.compile()

    async def consume():
        return [
            event
            async for event in graph.astream_events(
                {"messages": [HumanMessage(content="Remember me", id="m1")]},
                version="v2",
            )
        ]

    try:
        events = await asyncio.wait_for(consume(), timeout=1)
        assert not service.extraction_calls
    finally:
        release.set()
        await drain_memory_tasks()
    assert service.extraction_calls == ["t"]
    assert not any(event["event"].startswith("on_chat_model") for event in events)

"""Tests for the memory tools. / 记忆工具的测试。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast

import pytest
from langchain.tools import ToolRuntime
from zharness.memory import service as service_module
from zharness.memory import tools as tools_module
from zharness.memory.tools import (
    memory_add,
    memory_delete,
    memory_search,
    memory_update,
)


class _FakeService:
    def __init__(self) -> None:
        self.added: list[dict] = []

    async def add_fact(
        self,
        content,
        category="context",
        confidence=0.7,
        *,
        thread_id=None,
        source_type="tool",
    ):
        self.added.append(
            {
                "content": content,
                "category": category,
                "thread_id": thread_id,
                "source_type": source_type,
            }
        )
        return {"id": "fact-1", "status": "added"}

    async def update_fact(
        self, fact_id, *, content=None, category=None, confidence=None
    ):
        return {"id": fact_id, "status": "updated"}

    async def delete_fact(self, fact_id):
        return {"id": fact_id, "status": "deleted"}

    async def search(self, query, category=None, limit=None):
        return {
            "results": [{"id": "fact-1", "content": query, "category": "context"}],
            "count": 1,
        }


def runtime_for(thread_id: str | None) -> ToolRuntime:
    return cast(
        ToolRuntime,
        SimpleNamespace(execution_info=SimpleNamespace(thread_id=thread_id)),
    )


def test_runtime_is_hidden_from_memory_schemas() -> None:
    assert set(memory_search.args) == {"query", "category", "limit"}
    assert set(memory_add.args) == {"content", "category", "confidence"}
    assert set(memory_update.args) == {"fact_id", "content", "category", "confidence"}
    assert set(memory_delete.args) == {"fact_id"}


@pytest.mark.asyncio
async def test_memory_add_routes_through_service(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(tools_module, "get_memory_service", lambda: service)

    result = json.loads(
        await memory_add.coroutine(
            "Likes Go",
            category="preference",
            runtime=runtime_for("thread-1"),
        )
    )

    assert result == {"id": "fact-1", "status": "added"}
    assert service.added == [
        {
            "content": "Likes Go",
            "category": "preference",
            "thread_id": "thread-1",
            "source_type": "tool",
        }
    ]


@pytest.mark.asyncio
async def test_memory_search_returns_results(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(tools_module, "get_memory_service", lambda: service)

    result = json.loads(
        await memory_search.coroutine("preferences", runtime=runtime_for("t1"))
    )

    assert result["count"] == 1
    assert result["results"][0]["content"] == "preferences"


@pytest.mark.asyncio
async def test_memory_update_and_delete(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(tools_module, "get_memory_service", lambda: service)

    updated = json.loads(
        await memory_update.coroutine(
            "fact-1", content="New", runtime=runtime_for("t1")
        )
    )
    deleted = json.loads(
        await memory_delete.coroutine("fact-1", runtime=runtime_for("t1"))
    )

    assert updated == {"id": "fact-1", "status": "updated"}
    assert deleted == {"id": "fact-1", "status": "deleted"}


@pytest.mark.asyncio
async def test_tools_never_raise_on_unavailable_service(monkeypatch) -> None:
    def unavailable():
        raise service_module.MemoryUnavailableError("db down")

    monkeypatch.setattr(tools_module, "get_memory_service", unavailable)

    result = json.loads(
        await memory_add.coroutine("anything", runtime=runtime_for("t1"))
    )

    assert "error" in result
    assert "db down" in result["error"]

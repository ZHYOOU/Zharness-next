"""Tests for thread-bound knowledge tools. / 线程绑定知识工具测试。"""

import json
from types import SimpleNamespace
from typing import cast

import pytest
from langchain.tools import ToolRuntime
from zharness.knowledge import tools as tools_module
from zharness.knowledge.tools import (
    knowledge_delete,
    knowledge_ingest,
    knowledge_list,
    knowledge_search,
)


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def search(self, thread_id, query, *, limit=None):
        self.calls.append(("search", thread_id, query, limit))
        return {"results": [], "count": 0}

    async def ingest(self, thread_id, paths, *, replace=False):
        self.calls.append(("ingest", thread_id, paths, replace))
        return {"documents": [], "count": 0}

    async def list_documents(self, thread_id):
        self.calls.append(("list", thread_id))
        return {"documents": [], "count": 0}

    async def delete_document(self, thread_id, document_id):
        self.calls.append(("delete", thread_id, document_id))
        return {"id": document_id, "status": "deleted"}


def _runtime(thread_id: str | None) -> ToolRuntime:
    return cast(
        ToolRuntime,
        SimpleNamespace(execution_info=SimpleNamespace(thread_id=thread_id)),
    )


def test_runtime_and_strategy_are_hidden_from_tool_schemas() -> None:
    assert set(knowledge_search.args) == {"query", "limit"}
    assert set(knowledge_ingest.args) == {"paths", "replace"}
    assert set(knowledge_list.args) == set()
    assert set(knowledge_delete.args) == {"document_id"}


@pytest.mark.asyncio
async def test_tools_bind_every_operation_to_runtime_thread(monkeypatch) -> None:
    service = FakeService()
    monkeypatch.setattr(tools_module, "get_knowledge_service", lambda: service)

    await knowledge_search.coroutine("query", runtime=_runtime("thread-a"))
    await knowledge_ingest.coroutine(
        ["/workspace/a.md"],
        replace=True,
        runtime=_runtime("thread-a"),
    )
    await knowledge_list.coroutine(runtime=_runtime("thread-a"))
    await knowledge_delete.coroutine("doc-a", runtime=_runtime("thread-a"))

    assert service.calls == [
        ("search", "thread-a", "query", None),
        ("ingest", "thread-a", ["/workspace/a.md"], True),
        ("list", "thread-a"),
        ("delete", "thread-a", "doc-a"),
    ]


@pytest.mark.asyncio
async def test_tool_fails_closed_without_thread_identity(monkeypatch) -> None:
    service = FakeService()
    monkeypatch.setattr(tools_module, "get_knowledge_service", lambda: service)

    result = json.loads(
        await knowledge_search.coroutine("query", runtime=_runtime(None))
    )

    assert result == {"error": "server thread identity is unavailable"}
    assert service.calls == []

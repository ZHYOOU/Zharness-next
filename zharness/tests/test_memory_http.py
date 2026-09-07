"""Exercise the memory management HTTP contract. / 验证记忆管理 HTTP 契约。"""

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from test_memory_service import FakeMemoryRepository
from zharness.memory.service import MemoryService
from zharness.server import memory


@pytest.fixture
def repository(monkeypatch):
    """Use the real service with isolated storage. / 使用真实服务及隔离存储。"""
    repository = FakeMemoryRepository()
    monkeypatch.setattr(memory, "get_memory_service", lambda: MemoryService(repository))
    monkeypatch.setattr(
        memory,
        "get_settings",
        lambda: SimpleNamespace(
            memory=SimpleNamespace(
                enabled=False,
                extraction_enabled=True,
                injection_enabled=True,
                max_facts=200,
            )
        ),
    )
    return repository


@pytest.mark.asyncio
async def test_memory_crud(repository):
    async with AsyncClient(
        transport=ASGITransport(app=Starlette(routes=memory.routes)),
        base_url="http://test",
    ) as client:
        assert (await client.get("/memory/status")).json()["enabled"] is False
        assert (await client.get("/memory")).json() == {"facts": [], "profile": None}
        response = await client.post(
            "/memory",
            json={"content": "Prefer concise answers", "category": "preference"},
        )
        assert response.status_code == 201
        fact_id = response.json()["id"]
        assert (
            await client.post("/memory", json={"content": "Prefer concise answers"})
        ).status_code == 409
        response = await client.get("/memory")
        assert response.json()["facts"][0]["source_type"] == "manual"
        assert repository.facts[fact_id].access_count == 0
        assert (
            await client.put(
                f"/memory/{fact_id}",
                json={"content": "Prefer detailed answers", "category": "preference"},
            )
        ).status_code == 200
        assert (await client.get("/memory")).json()["facts"][0][
            "content"
        ] == "Prefer detailed answers"
        assert (await client.delete(f"/memory/{fact_id}")).status_code == 200
        assert (await client.delete(f"/memory/{fact_id}")).status_code == 404
        assert (await client.get("/memory")).json()["facts"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"content": " "},
        {"content": "x", "category": "invalid"},
        {"content": "x", "confidence": 2},
        {"content": "x", "unexpected": True},
    ],
)
async def test_invalid_input(repository, body):
    async with AsyncClient(
        transport=ASGITransport(app=Starlette(routes=memory.routes)),
        base_url="http://test",
    ) as client:
        assert (await client.post("/memory", json=body)).status_code == 422
        assert repository.facts == {}


@pytest.mark.asyncio
async def test_storage_failure(repository, monkeypatch):
    async def fail():
        raise RuntimeError("private database address")

    monkeypatch.setattr(repository, "setup", fail)
    async with AsyncClient(
        transport=ASGITransport(app=Starlette(routes=memory.routes)),
        base_url="http://test",
    ) as client:
        response = await client.get("/memory")
        assert response.status_code == 503
        assert "private database address" not in response.text

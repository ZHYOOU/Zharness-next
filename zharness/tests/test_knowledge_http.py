"""Exercise reusable knowledge-base HTTP endpoints. / 验证可复用知识库 HTTP 接口。"""

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from zharness.server import knowledge


class FakeKnowledgeService:
    """Record API calls without requiring PostgreSQL. / 在无需 PostgreSQL 的情况下记录 API 调用。"""

    def __init__(self) -> None:
        self.base_id = "a" * 32
        self.bindings: list[str] = []

    async def list_knowledge_bases(self):
        return {"knowledge_bases": []}

    async def create_knowledge_base(self, name, description):
        return {"id": self.base_id, "name": name, "description": description}

    async def update_knowledge_base(self, base_id, name, description):
        return {"id": base_id, "name": name, "description": description}

    async def delete_knowledge_base(self, base_id):
        return {"id": base_id, "status": "deleted"}

    async def list_knowledge_base_documents(self, base_id):
        return {"count": 0, "documents": []}

    async def add_knowledge_base_document(
        self, base_id, filename, content, *, replace=False
    ):
        return {
            "document_id": "b" * 32,
            "source_uri": filename,
            "status": "indexed",
            "replace": replace,
            "size": len(content),
        }

    async def delete_knowledge_base_document(self, base_id, document_id):
        return {"id": document_id, "status": "deleted"}

    async def get_bindings(self, thread_id):
        return {"thread_id": thread_id, "knowledge_base_ids": self.bindings}

    async def set_bindings(self, thread_id, base_ids):
        self.bindings = base_ids
        return {"thread_id": thread_id, "knowledge_base_ids": base_ids}


@pytest.fixture
def service(monkeypatch):
    """Enable knowledge and inject a fake service. / 启用知识库并注入伪服务。"""
    value = FakeKnowledgeService()
    monkeypatch.setattr(knowledge, "get_knowledge_service", lambda: value)
    monkeypatch.setattr(
        knowledge,
        "get_settings",
        lambda: SimpleNamespace(knowledge=SimpleNamespace(enabled=True)),
    )
    return value


@pytest.mark.asyncio
async def test_knowledge_base_crud_upload_and_bindings(service):
    async with AsyncClient(
        transport=ASGITransport(app=Starlette(routes=knowledge.routes)),
        base_url="http://test",
    ) as client:
        created = await client.post(
            "/knowledge/bases", json={"name": "Docs", "description": "Reference"}
        )
        assert created.status_code == 201
        base_id = created.json()["id"]
        assert (await client.get("/knowledge/bases")).status_code == 200
        assert (
            await client.put(
                f"/knowledge/bases/{base_id}",
                json={"name": "Updated", "description": "New"},
            )
        ).status_code == 200
        uploaded = await client.post(
            f"/knowledge/bases/{base_id}/documents",
            json={"filename": "notes.md", "content": "hello", "replace": True},
        )
        assert uploaded.status_code == 201
        assert uploaded.json()["replace"] is True
        thread_id = "thread-1"
        response = await client.put(
            f"/knowledge/threads/{thread_id}/bindings",
            json={"knowledge_base_ids": [base_id]},
        )
        assert response.json()["knowledge_base_ids"] == [base_id]
        assert (await client.get(f"/knowledge/threads/{thread_id}/bindings")).json()[
            "knowledge_base_ids"
        ] == [base_id]
        assert (
            await client.delete(f"/knowledge/bases/{base_id}/documents/{'b' * 32}")
        ).status_code == 200
        assert (await client.delete(f"/knowledge/bases/{base_id}")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "method", "body"),
    [
        ("/knowledge/bases", "POST", {"name": " "}),
        ("/knowledge/bases/not-an-id", "PUT", {"name": "Valid"}),
        (
            f"/knowledge/bases/{'a' * 32}/documents",
            "POST",
            {"filename": "../secret", "content": "text"},
        ),
        (
            "/knowledge/threads/bad%20thread/bindings",
            "PUT",
            {"knowledge_base_ids": []},
        ),
        (
            "/knowledge/threads/thread-1/bindings",
            "PUT",
            {"knowledge_base_ids": ["bad"]},
        ),
    ],
)
async def test_invalid_knowledge_input(service, path, method, body):
    async with AsyncClient(
        transport=ASGITransport(app=Starlette(routes=knowledge.routes)),
        base_url="http://test",
    ) as client:
        response = await client.request(method, path, json=body)
        assert response.status_code == 422

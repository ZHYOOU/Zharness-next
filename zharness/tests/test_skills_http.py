"""Exercise skill HTTP operations against real storage. / 使用真实存储验证技能 HTTP 操作。"""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from zharness.server import skills
from zharness.skills import LocalSkillStorage, SkillState


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """Isolate packages and runtime state. / 隔离技能包与运行时状态。"""
    root = tmp_path / "skills"
    state = SkillState(tmp_path / "state.json")
    storage = LocalSkillStorage(root, state=state)
    monkeypatch.setattr(skills, "LocalSkillStorage", lambda: storage)
    monkeypatch.setattr(skills, "SkillState", lambda: state)
    return storage


@pytest.mark.asyncio
async def test_create_read_toggle_and_duplicate(storage):
    async with AsyncClient(
        transport=ASGITransport(app=Starlette(routes=skills.routes)),
        base_url="http://test",
    ) as client:
        assert (await client.get("/skills")).json() == {"skills": []}
        body = {
            "name": "test-skill",
            "description": "测试：描述",
            "content": "# Instructions\nDo the task.",
        }
        response = await client.post("/skills", json=body)
        assert response.status_code == 201
        assert response.json()["category"] == "user"
        assert response.json()["enabled"] is True
        assert len(storage.load_skills(enabled_only=True)) == 1
        detail = (await client.get("/skills/test-skill")).json()
        assert body["content"] in detail["content"]
        assert "skill_file" not in detail
        assert (await client.post("/skills", json=body)).status_code == 409
        assert (
            await client.patch("/skills/test-skill", json={"enabled": False})
        ).json()["enabled"] is False
        assert storage.load_skills(enabled_only=True) == []
        assert (await client.get("/skills")).json()["skills"][0]["enabled"] is False
        assert (
            await client.patch("/skills/test-skill", json={"enabled": True})
        ).status_code == 200
        assert len(storage.load_skills(enabled_only=True)) == 1
        assert (await client.get("/skills/missing")).status_code == 404
        assert (
            await client.patch("/skills/missing", json={"enabled": False})
        ).status_code == 404
        for enabled in ["false", 0, None]:
            assert (
                await client.patch("/skills/test-skill", json={"enabled": enabled})
            ).status_code == 422
        assert (await client.post("/skills", content="{")).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "patch",
    [
        {"name": "../escape"},
        {"name": "UPPER"},
        {"description": " "},
        {"description": "<tag>"},
        {"content": " "},
        {"extra": True},
    ],
)
async def test_invalid_create(storage, patch):
    async with AsyncClient(
        transport=ASGITransport(app=Starlette(routes=skills.routes)),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/skills",
            json={
                "name": "valid",
                "description": "Description",
                "content": "Instructions",
                **patch,
            },
        )
        assert response.status_code == 422
        assert storage.load_skills() == []


@pytest.mark.asyncio
async def test_symlink_and_storage_failure(storage, tmp_path, monkeypatch):
    root = storage.get_skills_root_path()
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "user").symlink_to(outside)
    async with AsyncClient(
        transport=ASGITransport(app=Starlette(routes=skills.routes)),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/skills",
            json={
                "name": "valid",
                "description": "Description",
                "content": "Instructions",
            },
        )
        assert response.status_code == 409
        assert list(outside.iterdir()) == []

        def fail():
            raise OSError("private host path")

        monkeypatch.setattr(storage, "load_skills", fail)
        response = await client.get("/skills")
        assert response.status_code == 503
        assert "private host path" not in response.text

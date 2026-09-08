"""Skill management HTTP endpoints. / 技能管理 HTTP 接口。"""

import json
from dataclasses import replace
from threading import Lock

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from zharness.skills import LocalSkillStorage, SkillState, SkillStateError
from zharness.skills.parser import parse_skill_file
from zharness.skills.types import Skill, SkillCategory
from zharness.skills.validation import validate_skill_name

_mutation_lock = Lock()


class SkillInput(BaseModel):
    """Validate a new user skill. / 校验新建的自定义技能。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1024, pattern=r"^[^<>]+$")
    content: str = Field(min_length=1, max_length=100000)


class SkillToggle(BaseModel):
    """Require an explicit boolean state. / 要求明确的布尔状态。"""

    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


def serialize_skill(skill: Skill) -> dict:
    """Return public metadata without host paths. / 返回不含宿主路径的公开元数据。"""
    return {
        "name": skill.name,
        "description": skill.description,
        "category": skill.category.value,
        "enabled": skill.enabled,
        "license": skill.license,
        "allowed_tools": skill.allowed_tools,
    }


def handle_skills(method: str, name: str | None, data: dict) -> JSONResponse:
    """Use the runtime registry and serialize filesystem mutations. / 使用运行时注册表并串行化文件系统修改。"""
    with _mutation_lock:
        storage = LocalSkillStorage()
        skills = storage.load_skills()
        if method == "POST":
            fields = SkillInput.model_validate(data)
            normalized = validate_skill_name(fields.name)
            if any(skill.name == normalized for skill in skills):
                return JSONResponse({"error": "技能名称已存在。"}, status_code=409)
            root = storage.get_skills_root_path()
            user_root = root / "user"
            if user_root.is_symlink():
                return JSONResponse(
                    {"error": "自定义技能目录不可用。"}, status_code=409
                )
            user_root.mkdir(parents=True, exist_ok=True)
            directory = user_root / normalized
            try:
                directory.mkdir()
            except FileExistsError:
                return JSONResponse({"error": "技能目录已存在。"}, status_code=409)
            file = directory / "SKILL.md"
            markdown = (
                "---\n"
                + yaml.safe_dump(
                    {"name": normalized, "description": fields.description},
                    allow_unicode=True,
                    sort_keys=False,
                )
                + "---\n\n"
                + fields.content
                + "\n"
            )
            try:
                file.write_text(markdown, encoding="utf-8")
            except OSError:
                file.unlink(missing_ok=True)
                directory.rmdir()
                raise
            skill = parse_skill_file(file, SkillCategory.USER)
            if skill is None:
                raise ValueError("技能内容无法解析。")
            return JSONResponse(
                serialize_skill(
                    replace(skill, enabled=SkillState().is_enabled(normalized))
                ),
                status_code=201,
            )
        if name is None:
            return JSONResponse(
                {"skills": [serialize_skill(skill) for skill in skills]}
            )
        skill = next((skill for skill in skills if skill.name == name), None)
        if skill is None:
            return JSONResponse({"error": "技能不存在，请刷新列表。"}, status_code=404)
        if method == "PATCH":
            fields = SkillToggle.model_validate(data)
            SkillState().set_enabled(skill.name, fields.enabled)
            return JSONResponse(serialize_skill(replace(skill, enabled=fields.enabled)))
        root = storage.get_skills_root_path().resolve()
        if not skill.skill_file.resolve().is_relative_to(root):
            return JSONResponse({"error": "技能文件路径不可用。"}, status_code=409)
        return JSONResponse(
            {
                **serialize_skill(skill),
                "content": skill.skill_file.read_text(encoding="utf-8"),
            }
        )


async def skills_endpoint(request: Request) -> JSONResponse:
    """Expose discovery, creation, detail and persistent toggles. / 提供发现、创建、详情与持久化启停接口。"""
    try:
        data = await request.json() if request.method in {"POST", "PATCH"} else {}
        return await run_in_threadpool(
            handle_skills, request.method, request.path_params.get("name"), data
        )
    except (ValidationError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return JSONResponse(
            {"error": "请输入有效的技能名称、描述、正文或布尔启停状态。"},
            status_code=422,
        )
    except (OSError, SkillStateError):
        return JSONResponse(
            {"error": "技能存储暂时不可用，请检查后端目录权限。"}, status_code=503
        )


routes = [
    Route("/skills", skills_endpoint, methods=["GET", "POST"]),
    Route("/skills/{name}", skills_endpoint, methods=["GET", "PATCH"]),
]

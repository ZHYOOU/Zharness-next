"""Memory management HTTP endpoints. / 记忆管理 HTTP 接口。"""

import json
from dataclasses import asdict

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from zharness.config import get_settings
from zharness.memory.service import MemoryUnavailableError, get_memory_service
from zharness.memory.types import FactCategory


class FactInput(BaseModel):
    """Validate manual memory edits. / 校验手动记忆编辑。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    content: str = Field(min_length=1, max_length=10000)
    category: FactCategory = FactCategory.CONTEXT
    confidence: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False)


async def memory_endpoint(request: Request) -> JSONResponse:
    """Expose persistent facts and the generated profile. / 暴露持久化事实及自动生成的画像。"""
    settings = get_settings().memory
    if request.url.path.endswith("/status"):
        return JSONResponse(
            {
                "enabled": settings.enabled,
                "extraction_enabled": settings.extraction_enabled,
                "injection_enabled": settings.injection_enabled,
                "max_facts": settings.max_facts,
            }
        )
    try:
        service = get_memory_service()
        if request.method == "GET":
            facts = await service.list_facts()
            profile = await service.get_profile()
            profile_data = asdict(profile) if profile else None
            if profile_data and profile.updated_at:
                profile_data["updated_at"] = profile.updated_at.isoformat()
            return JSONResponse(
                {"facts": [fact.to_dict() for fact in facts], "profile": profile_data}
            )
        if request.method == "DELETE":
            result = await service.delete_fact(request.path_params["fact_id"])
        else:
            try:
                data = FactInput.model_validate(await request.json())
            except (ValidationError, json.JSONDecodeError, UnicodeDecodeError):
                return JSONResponse(
                    {"error": "请输入有效的记忆内容、分类及 0 到 1 之间的置信度。"},
                    status_code=422,
                )
            if request.method == "POST":
                result = await service.add_fact(**data.model_dump())
            else:
                result = await service.update_fact(
                    request.path_params["fact_id"], **data.model_dump()
                )
        if "error" in result:
            status = 404 if result["error"] == "Fact not found" else 409
            return JSONResponse(result, status_code=status)
        return JSONResponse(
            result, status_code=201 if request.method == "POST" else 200
        )
    except MemoryUnavailableError:
        return JSONResponse(
            {"error": "记忆存储暂时不可用，请检查后端数据库连接。"}, status_code=503
        )


routes = [
    Route("/memory/status", memory_endpoint, methods=["GET"]),
    Route("/memory", memory_endpoint, methods=["GET", "POST"]),
    Route("/memory/{fact_id}", memory_endpoint, methods=["PUT", "DELETE"]),
]

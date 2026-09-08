"""Knowledge-base management HTTP endpoints. / 知识库管理 HTTP 接口。"""

import json
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from zharness.config import get_settings
from zharness.host.paths import THREAD_ID_PATTERN
from zharness.knowledge import get_knowledge_service
from zharness.knowledge.service import KnowledgeUnavailableError

_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
_THREAD_PATTERN = re.compile(rf"^{THREAD_ID_PATTERN}$")


class KnowledgeBaseInput(BaseModel):
    """Validate knowledge-base metadata. / 校验知识库元数据。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)


class KnowledgeDocumentInput(BaseModel):
    """Validate a browser-uploaded UTF-8 document. / 校验浏览器上传的 UTF-8 文档。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    filename: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=10_000_000)
    replace: bool = False

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        """Accept a plain filename without path components. / 仅接受不含路径部分的文件名。"""
        if value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("filename must not contain a path")
        return value


class KnowledgeBindingsInput(BaseModel):
    """Validate a complete binding selection. / 校验完整的绑定选择。"""

    model_config = ConfigDict(extra="forbid")
    knowledge_base_ids: list[str] = Field(max_length=100)

    @field_validator("knowledge_base_ids")
    @classmethod
    def validate_ids(cls, values: list[str]) -> list[str]:
        """Require canonical knowledge-base identifiers. / 要求规范的知识库标识符。"""
        if any(not _ID_PATTERN.fullmatch(value) for value in values):
            raise ValueError("invalid knowledge base id")
        return values


def _status(result: dict, success: int = 200) -> JSONResponse:
    """Map service not-found results to HTTP responses. / 将服务层未找到结果映射为 HTTP 响应。"""
    if "error" in result:
        return JSONResponse(
            {"error": "知识库或文档不存在，请刷新后重试。"}, status_code=404
        )
    return JSONResponse(result, status_code=success)


async def knowledge_endpoint(request: Request) -> JSONResponse:
    """Manage reusable knowledge bases, documents and thread bindings. / 管理可复用知识库、文档与会话绑定。"""
    if not get_settings().knowledge.enabled:
        return JSONResponse({"error": "后端尚未启用知识库功能。"}, status_code=503)
    service = get_knowledge_service()
    base_id = request.path_params.get("base_id")
    document_id = request.path_params.get("document_id")
    thread_id = request.path_params.get("thread_id")
    try:
        if thread_id is not None:
            if not _THREAD_PATTERN.fullmatch(thread_id):
                return JSONResponse({"error": "会话标识无效。"}, status_code=422)
            if request.method == "GET":
                return _status(await service.get_bindings(thread_id))
            data = KnowledgeBindingsInput.model_validate(await request.json())
            return _status(
                await service.set_bindings(thread_id, data.knowledge_base_ids)
            )

        if base_id is None:
            if request.method == "GET":
                return _status(await service.list_knowledge_bases())
            data = KnowledgeBaseInput.model_validate(await request.json())
            return _status(
                await service.create_knowledge_base(data.name, data.description), 201
            )

        if not _ID_PATTERN.fullmatch(base_id):
            return JSONResponse({"error": "知识库标识无效。"}, status_code=422)
        if document_id is not None:
            if not _ID_PATTERN.fullmatch(document_id):
                return JSONResponse({"error": "文档标识无效。"}, status_code=422)
            return _status(
                await service.delete_knowledge_base_document(base_id, document_id)
            )
        if request.url.path.endswith("/documents"):
            if request.method == "GET":
                return _status(await service.list_knowledge_base_documents(base_id))
            data = KnowledgeDocumentInput.model_validate(await request.json())
            return _status(
                await service.add_knowledge_base_document(
                    base_id,
                    data.filename,
                    data.content,
                    replace=data.replace,
                ),
                201,
            )
        if request.method == "DELETE":
            return _status(await service.delete_knowledge_base(base_id))
        data = KnowledgeBaseInput.model_validate(await request.json())
        return _status(
            await service.update_knowledge_base(base_id, data.name, data.description)
        )
    except (ValidationError, json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "请求中的知识库数据无效。"}, status_code=422)
    except KnowledgeUnavailableError:
        return JSONResponse(
            {"error": "知识库服务暂时不可用，请检查数据库和嵌入模型配置。"},
            status_code=503,
        )


routes = [
    Route("/knowledge/bases", knowledge_endpoint, methods=["GET", "POST"]),
    Route(
        "/knowledge/bases/{base_id}",
        knowledge_endpoint,
        methods=["PUT", "DELETE"],
    ),
    Route(
        "/knowledge/bases/{base_id}/documents",
        knowledge_endpoint,
        methods=["GET", "POST"],
    ),
    Route(
        "/knowledge/bases/{base_id}/documents/{document_id}",
        knowledge_endpoint,
        methods=["DELETE"],
    ),
    Route(
        "/knowledge/threads/{thread_id}/bindings",
        knowledge_endpoint,
        methods=["GET", "PUT"],
    ),
]

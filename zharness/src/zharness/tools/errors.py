"""Stable error payloads for agent-facing tools. / 面向 Agent 工具的稳定错误载荷。"""

from __future__ import annotations

import json
from enum import StrEnum


class ToolErrorCode(StrEnum):
    """Machine-readable categories shared by agent tools. / Agent 工具共用的机器可读错误分类。"""

    INVALID_CONTEXT = "invalid_context"
    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    UNAVAILABLE = "unavailable"
    OPERATION_FAILED = "operation_failed"


def serialize_tool_error(
    code: ToolErrorCode,
    message: str,
    *,
    retryable: bool = False,
) -> str:
    """Serialize a stable tool failure without exposing internal exceptions.

    序列化稳定的工具失败信息，且不暴露内部异常。
    """
    return json.dumps(
        {
            "error": message,
            "error_code": code.value,
            "retryable": retryable,
        },
        ensure_ascii=False,
    )

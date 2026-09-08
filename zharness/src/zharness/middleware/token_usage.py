"""Collect and propagate model token usage. / 收集并传递模型 token 用量。"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

SUBAGENT_TOKEN_USAGE_KEY = "subagent_token_usage"
SUBAGENT_TOKEN_USAGE_ATTRIBUTED_KEY = "subagent_token_usage_attributed"


def _non_negative_int(value: Any) -> int | None:
    """Return a non-negative integer token count. / 返回非负整数 token 数。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def normalize_token_usage(value: Any) -> dict[str, int] | None:
    """Validate provider usage metadata into a stable shape. / 将提供商用量元数据校验为稳定结构。"""
    if not isinstance(value, Mapping):
        return None
    input_tokens = _non_negative_int(value.get("input_tokens"))
    output_tokens = _non_negative_int(value.get("output_tokens"))
    total_tokens = _non_negative_int(value.get("total_tokens"))
    if input_tokens is None or output_tokens is None:
        return None
    if total_tokens is None:
        total_tokens = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def accumulate_token_usage(messages: Sequence[BaseMessage]) -> dict[str, int] | None:
    """Sum usage across unique AI messages. / 汇总唯一 AI 消息的 token 用量。"""
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    found = False
    counted_ids: set[str] = set()
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        if message.id:
            if message.id in counted_ids:
                continue
            counted_ids.add(message.id)
        usage = normalize_token_usage(message.usage_metadata)
        if usage is None:
            continue
        found = True
        for key in totals:
            totals[key] += usage[key]
    return totals if found else None


def _has_tool_call(message: AIMessage, tool_call_id: str) -> bool:
    """Check whether an AI message owns a tool call. / 检查 AI 消息是否拥有指定工具调用。"""
    return any(call.get("id") == tool_call_id for call in message.tool_calls)


class TokenUsageMiddleware(AgentMiddleware):
    """Log model usage and fold delegated usage into parent history. / 记录模型用量并将委派用量归入父级历史。"""

    def _apply(self, state: AgentState) -> dict[str, list[BaseMessage]] | None:
        """Process usage after one model call. / 在一次模型调用后处理用量。"""
        messages = state.get("messages", [])
        if not messages:
            return None

        updates: dict[int, BaseMessage] = {}
        if isinstance(messages[-1], AIMessage):
            raw_usage = messages[-1].usage_metadata
            usage = normalize_token_usage(raw_usage)
            if usage is not None:
                detail_parts: list[str] = []
                for key in ("input_token_details", "output_token_details"):
                    details = raw_usage.get(key) if raw_usage else None
                    if details:
                        detail_parts.append(f"{key}={details}")
                detail_suffix = f" {' '.join(detail_parts)}" if detail_parts else ""
                logger.info(
                    "LLM token usage: input=%s output=%s total=%s%s",
                    usage["input_tokens"],
                    usage["output_tokens"],
                    usage["total_tokens"],
                    detail_suffix,
                )

        # Only adjacent tool results belong to the model turn that just finished.
        # 只有相邻工具结果属于刚结束的模型轮次。
        index = len(messages) - 2
        while index >= 0 and isinstance(messages[index], ToolMessage):
            tool_message = messages[index]
            if tool_message.additional_kwargs.get(SUBAGENT_TOKEN_USAGE_ATTRIBUTED_KEY):
                index -= 1
                continue
            usage = normalize_token_usage(
                tool_message.additional_kwargs.get(SUBAGENT_TOKEN_USAGE_KEY)
            )
            if usage is None or not tool_message.tool_call_id:
                index -= 1
                continue

            dispatch_index = index - 1
            while dispatch_index >= 0:
                candidate = messages[dispatch_index]
                if isinstance(candidate, AIMessage) and _has_tool_call(
                    candidate, tool_message.tool_call_id
                ):
                    current = updates.get(dispatch_index, candidate)
                    current_usage = dict(getattr(current, "usage_metadata", None) or {})
                    normalized_current = normalize_token_usage(current_usage) or {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                    }
                    for key in normalized_current:
                        current_usage[key] = normalized_current[key] + usage[key]
                    updates[dispatch_index] = current.model_copy(
                        update={"usage_metadata": current_usage}
                    )
                    metadata = dict(tool_message.additional_kwargs)
                    metadata[SUBAGENT_TOKEN_USAGE_ATTRIBUTED_KEY] = True
                    updates[index] = tool_message.model_copy(
                        update={"additional_kwargs": metadata}
                    )
                    break
                dispatch_index -= 1
            index -= 1

        if not updates:
            return None
        return {"messages": [updates[key] for key in sorted(updates)]}

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        """Collect usage after a synchronous model call. / 在同步模型调用后收集用量。"""
        return self._apply(state)

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        """Collect usage after an asynchronous model call. / 在异步模型调用后收集用量。"""
        return self._apply(state)


__all__ = [
    "SUBAGENT_TOKEN_USAGE_ATTRIBUTED_KEY",
    "SUBAGENT_TOKEN_USAGE_KEY",
    "TokenUsageMiddleware",
    "accumulate_token_usage",
    "normalize_token_usage",
]

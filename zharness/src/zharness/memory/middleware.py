"""Long-term memory middleware for the lead agent. / 主智能体的长期记忆中间件。

Three responsibilities:
- Register the ``memory_*`` tools so the agent can recall and edit facts on demand.
- Inject the top facts and user profile into every asynchronous model call as a
  hidden memory block (``awrap_model_call``).
- Extract new memories after each completed turn (``aafter_agent``), advancing a
  per-thread watermark only after a successful write so failures retry next turn.

三项职责：
- 注册 ``memory_*`` 工具，供 agent 按需召回与编辑事实。
- 在每次异步模型调用时，以隐藏记忆块注入顶级事实与用户画像（``awrap_model_call``）。
- 每轮结束后抽取新记忆（``aafter_agent``），仅写入成功后推进线程级水位，失败在下一轮重试。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse, ResponseT
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage

from zharness.memory.extraction import extract_memories
from zharness.memory.injection import format_memory_injection
from zharness.memory.service import MemoryUnavailableError, get_memory_service
from zharness.memory.tools import (
    memory_add,
    memory_delete,
    memory_search,
    memory_update,
)

logger = logging.getLogger(__name__)

_EXISTING_FACTS_CAP = 100
"""Facts fed back into the extraction prompt to avoid re-adding known entries. / 反馈给抽取提示词以避免重复添加的既有事实上限。"""


class MemoryMiddleware(AgentMiddleware[Any, Any, ResponseT]):
    """Persist, recall, and inject long-term memory for the lead agent.

    为主智能体持久化、召回并注入长期记忆。
    """

    def __init__(
        self,
        model: Any,
        *,
        extraction_model: Any | None = None,
        enabled: bool = True,
        extraction_enabled: bool = True,
        injection_enabled: bool = True,
        user_id: str = "default",
        max_facts: int = 200,
        min_confidence: float = 0.7,
        inject_top_k: int = 8,
        search_limit: int = 10,
        gate_enabled: bool = True,
        injection_max_chars: int = 2000,
        extract_window: int = 8,
    ) -> None:
        """Initialize the memory middleware with model and policy settings.

        使用模型与策略配置初始化记忆中间件。
        """
        super().__init__()
        self._model = model
        self._extraction_model = extraction_model or model
        self._enabled = enabled
        self._extraction_enabled = extraction_enabled
        self._injection_enabled = injection_enabled
        self._user_id = user_id
        self._max_facts = max_facts
        self._min_confidence = min_confidence
        self._inject_top_k = inject_top_k
        self._search_limit = search_limit
        self._gate_enabled = gate_enabled
        self._injection_max_chars = injection_max_chars
        self._extract_window = extract_window
        self._watermarks: dict[str, str] = {}
        self._warned_config_error = False
        self.tools = [
            memory_search,
            memory_add,
            memory_update,
            memory_delete,
        ]

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Any,
    ) -> ModelResponse[ResponseT]:
        """Inject the memory block into asynchronous model calls. / 向异步模型调用注入记忆块。"""
        if self._enabled and self._injection_enabled:
            block = await self._memory_block()
            if block:
                request = request.override(
                    system_message=_append_system_message(
                        request.system_message,
                        block,
                    )
                )
        return await handler(request)

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Any,
    ) -> ModelResponse[ResponseT]:
        """Pass through synchronous model calls; the server runs asynchronously.

        同步模型调用直接透传；服务器以异步方式运行。
        """
        return handler(request)

    async def aafter_agent(
        self,
        state: dict[str, Any],
        runtime: Any,
    ) -> dict[str, Any] | None:
        """Extract new memories after a completed agent turn. / 在 agent 回合结束后抽取新记忆。"""
        if not (self._enabled and self._extraction_enabled):
            return None
        messages = list(state.get("messages") or [])
        thread_id = _thread_id(runtime)
        new_messages = self._new_messages(thread_id, messages)
        if not new_messages or not any(
            isinstance(message, HumanMessage) for message in new_messages
        ):
            return None
        try:
            service = get_memory_service()
        except RuntimeError:
            self._warn_config_error_once()
            return None
        try:
            existing = await service.top_facts(
                limit=_EXISTING_FACTS_CAP,
                min_confidence=0.0,
            )
            profile = await service.get_profile()
            result = await extract_memories(
                self._extraction_model,
                new_messages,
                existing,
                profile,
            )
            metrics = await service.apply_extraction(result, thread_id=thread_id)
            if metrics.get("added"):
                logger.info(
                    "Extracted %d memory facts from thread %s",
                    metrics["added"],
                    thread_id,
                )
            self._advance_watermark(thread_id, messages)
        except MemoryUnavailableError:
            logger.warning(
                "Memory extraction skipped; storage unavailable (thread %s)",
                thread_id,
            )
        except Exception:
            logger.exception(
                "Memory extraction failed; watermark not advanced, will retry"
            )
        return None

    def after_agent(
        self,
        state: dict[str, Any],
        runtime: Any,
    ) -> dict[str, Any] | None:
        """Pass through synchronous agent completion; the server runs asynchronously.

        同步 agent 完成直接透传；服务器以异步方式运行。
        """
        return None

    def _warn_config_error_once(self) -> None:
        """Warn once about an unconfigured memory backend instead of spamming.

        针对未配置的记忆后端只告警一次，避免每轮重复刷屏。
        """
        if not self._warned_config_error:
            logger.warning(
                "Long-term memory is enabled but PostgreSQL is not configured; "
                "set ZHARNESS_POSTGRES_URI or ZHARNESS_POSTGRES_MANAGED=true"
            )
            self._warned_config_error = True

    async def _memory_block(self) -> str | None:
        """Build the hidden memory context block, or ``None`` when unavailable.

        构建隐藏的记忆上下文块；不可用时返回 ``None``。
        """
        try:
            service = get_memory_service()
            facts = await service.top_facts(limit=self._inject_top_k)
            profile = await service.get_profile()
        except (MemoryUnavailableError, RuntimeError):
            logger.debug("Memory injection unavailable", exc_info=True)
            return None
        return format_memory_injection(
            profile,
            facts,
            max_chars=self._injection_max_chars,
        )

    def _new_messages(
        self,
        thread_id: str | None,
        messages: list[AnyMessage],
    ) -> list[AnyMessage]:
        """Return the user/assistant messages not yet extracted for this thread.

        返回该线程尚未抽取的用户/助手消息。

        Tool results are excluded: the extraction model only needs the
        conversational turns, and a window full of ``ToolMessage`` rows would
        violate the "first message is a user/assistant message" contract of
        OpenAI-compatible providers.

        排除工具结果：抽取模型只需要对话轮次，且全为 ``ToolMessage`` 的窗口会违反
        OpenAI 兼容提供商的“首条消息必须是用户/助手消息”约定。
        """
        if not messages:
            return []
        if thread_id is None:
            tail = messages
        else:
            watermark = self._watermarks.get(thread_id)
            if watermark is None:
                tail = messages
            else:
                tail = messages
                for index, message in enumerate(messages):
                    if str(message.id or "") == watermark:
                        tail = messages[index + 1 :]
                        break
        return [
            message
            for message in tail
            if isinstance(message, (HumanMessage, AIMessage))
        ][-self._extract_window :]

    def _advance_watermark(
        self,
        thread_id: str | None,
        messages: list[AnyMessage],
    ) -> None:
        """Remember the last message so it is not extracted again. / 记录最后一条消息，避免重复抽取。"""
        if thread_id is None or not messages:
            return
        self._watermarks[thread_id] = str(messages[-1].id or "")


def _append_system_message(
    system_message: SystemMessage | None,
    text: str,
) -> SystemMessage:
    """Append text to a model request's system message. / 将文本追加到模型请求的系统消息。"""
    if system_message is None:
        return SystemMessage(content=text)
    content = system_message.text
    separator = "\n\n" if content else ""
    return SystemMessage(
        content=f"{content}{separator}{text}",
        additional_kwargs=system_message.additional_kwargs,
        response_metadata=system_message.response_metadata,
        name=system_message.name,
        id=system_message.id,
    )


def _thread_id(runtime: Any) -> str | None:
    """Extract the current thread id from the runtime, if present. / 从运行时提取当前线程 id（如有）。"""
    info = runtime.execution_info if runtime is not None else None
    return info.thread_id if info is not None else None

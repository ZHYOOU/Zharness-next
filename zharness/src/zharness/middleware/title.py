"""Auto-generate the thread title after the first complete exchange.

After the first full user/assistant turn, this middleware writes a ``title``
into the agent state so the thread can be labelled in the UI. When a dedicated
title model is configured it calls that model; otherwise it falls back to a
local title derived from the first user message, so streaming is never blocked
by a second model call.

The ``title`` channel is added to the agent state schema and persisted with the
thread checkpoint. Both the synchronous and asynchronous ``after_agent`` hooks
are implemented: the sync path only produces a local fallback, while the async
path (used by the server) can invoke a title model.

首轮完整交互后，本中间件将 ``title`` 写入智能体状态，以便在线程列表中显示标题。
配置了专用标题模型时调用该模型；否则回退为首条用户消息派生的本地标题，从而避免
二次模型调用阻塞流式输出。

``title`` 通道被加入智能体状态 schema，并随线程检查点持久化。同步与异步
``after_agent`` 钩子均已实现：同步路径只产生本地后备标题，而异步路径（服务器使用）
可调用标题模型。

Hooks: after_agent (sync + async).
"""

from __future__ import annotations

import logging
import re
from typing import Any, NotRequired, override

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.internal_call_transformer import (
    InternalCallTransformer,
    internal_call_metadata,
)
from langchain.agents.middleware.types import AgentState
from langgraph.runtime import Runtime

from zharness.config.loader import get_settings
from zharness.config.settings import TitleSettings
from zharness.models.factory import create_chat_model

logger = logging.getLogger(__name__)

_TITLE_SOURCE = "title"
"""lc_source marker for the internal title-model call. / 标题模型内部调用的 lc_source 标记。"""


class TitleMiddlewareState(AgentState[Any]):
    """Agent state extended with the generated thread title. / 扩展了生成标题的智能体状态。"""

    title: NotRequired[str | None]


class TitleMiddleware(AgentMiddleware[TitleMiddlewareState]):
    """Persist a thread title generated after the first complete exchange.

    在首轮完整交互后持久化生成的线程标题。
    """

    state_schema = TitleMiddlewareState

    transformers = (InternalCallTransformer,)
    """Keep the title model call out of ``run.messages`` when streaming.

    流式输出时，将标题模型调用从 ``run.messages`` 中剔除。
    """

    def __init__(self, title_settings: TitleSettings | None = None) -> None:
        """Initialize the middleware with optional explicit settings.

        使用可选显式配置初始化中间件；未提供时按需读取全局配置。
        """
        super().__init__()
        self._title_settings = title_settings

    @property
    def title_settings(self) -> TitleSettings:
        """Return the resolved title settings. / 返回解析后的标题配置。"""
        if self._title_settings is not None:
            return self._title_settings
        return get_settings().title

    @staticmethod
    def _message_type(message: object) -> str | None:
        """Return a normalized message type, or ``None`` when unknown. / 返回规范化消息类型；未知时返回 ``None``。"""
        message_type = getattr(message, "type", None)
        if message_type is None and isinstance(message, dict):
            message_type = message.get("type") or message.get("role")
        if message_type == "user":
            return "human"
        if message_type == "assistant":
            return "ai"
        return message_type if isinstance(message_type, str) else None

    @staticmethod
    def _message_content(message: object) -> object:
        """Return the raw content of a message object or dict. / 返回消息对象或字典的原始内容。"""
        if isinstance(message, dict):
            return message.get("content", "")
        return getattr(message, "content", "")

    @staticmethod
    def _is_hidden_reminder(message: object) -> bool:
        """Return whether a message is a hidden injected reminder. / 返回消息是否为隐藏注入的提醒。"""
        additional_kwargs = None
        if isinstance(message, dict):
            additional_kwargs = message.get("additional_kwargs")
        else:
            additional_kwargs = getattr(message, "additional_kwargs", None)
        if not isinstance(additional_kwargs, dict):
            return False
        return bool(
            additional_kwargs.get("hide_from_ui")
            or additional_kwargs.get("zharness_dynamic_date")
        )

    def _is_user_message_for_title(self, message: object) -> bool:
        """Return whether a message counts as a real first user turn. / 返回消息是否为真实的首轮用户消息。"""
        return self._message_type(message) == "human" and not self._is_hidden_reminder(
            message
        )

    def _normalize_content(self, content: object) -> str:
        """Flatten structured message content into a plain string. / 将结构化消息内容扁平化为普通字符串。"""
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts = [self._normalize_content(item) for item in content]
            return "\n".join(part for part in parts if part)

        if isinstance(content, dict):
            text_value = content.get("text")
            if isinstance(text_value, str):
                return text_value
            nested_content = content.get("content")
            if nested_content is not None:
                return self._normalize_content(nested_content)

        return ""

    def _get_title_user_message(self, state: TitleMiddlewareState) -> str:
        """Return the first real user message as plain text. / 返回首条真实用户消息的纯文本。"""
        messages = state.get("messages") or []
        content = next(
            (
                self._message_content(message)
                for message in messages
                if self._is_user_message_for_title(message)
            ),
            "",
        )
        return self._normalize_content(content)

    def _should_generate_title(self, state: TitleMiddlewareState) -> bool:
        """Return whether a title should be generated for this state. / 返回该状态是否应生成标题。"""
        settings = self.title_settings
        if not settings.enabled:
            return False

        if state.get("title"):
            return False

        messages = state.get("messages") or []
        user_messages = [
            message for message in messages if self._is_user_message_for_title(message)
        ]
        assistant_messages = [
            message for message in messages if self._message_type(message) == "ai"
        ]

        return len(user_messages) == 1 and len(assistant_messages) >= 1

    def _build_title_prompt(self, state: TitleMiddlewareState) -> str:
        """Build the title-model prompt from the first exchange. / 根据首轮交互构建标题模型提示词。"""
        settings = self.title_settings
        messages = state.get("messages") or []

        assistant_content = next(
            (
                self._message_content(message)
                for message in messages
                if self._message_type(message) == "ai"
            ),
            "",
        )
        user_msg = self._get_title_user_message(state)
        assistant_msg = self._strip_think_tags(
            self._normalize_content(assistant_content)
        )

        return settings.prompt_template.format(
            max_words=settings.max_words,
            user_msg=user_msg[:500],
            assistant_msg=assistant_msg[:500],
        )

    @staticmethod
    def _strip_think_tags(text: str) -> str:
        """Remove model  thinking blocks from a string. / 从字符串中移除模型的 thinking 代码块。"""
        return re.sub(
            r" thinking[\s\S]*? response",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()

    def _parse_title(self, content: object) -> str:
        """Clean and cap the model-generated title. / 清洗并限制模型生成标题的长度。"""
        settings = self.title_settings
        title = self._strip_think_tags(self._normalize_content(content)).strip()
        title = title.strip('"').strip("'")
        if len(title) > settings.max_chars:
            return title[: settings.max_chars]
        return title

    def _fallback_title(self, user_msg: str) -> str:
        """Derive a local fallback title from the first user message. / 由首条用户消息派生本地后备标题。"""
        settings = self.title_settings
        fallback_chars = min(settings.max_chars, 50)
        if len(user_msg) > fallback_chars:
            ellipsis = "..."
            body = min(fallback_chars, settings.max_chars - len(ellipsis))
            return user_msg[:body].rstrip() + ellipsis
        return user_msg if user_msg else "New Conversation"

    def _generate_title_result(
        self, state: TitleMiddlewareState
    ) -> dict[str, Any] | None:
        """Compute a title synchronously (local fallback only). / 同步计算标题（仅本地后备）。"""
        if not self._should_generate_title(state):
            return None
        return {"title": self._fallback_title(self._get_title_user_message(state))}

    async def _agenerate_title_result(
        self, state: TitleMiddlewareState
    ) -> dict[str, Any] | None:
        """Compute a title asynchronously, invoking the title model when configured.

        异步计算标题；配置了标题模型时调用该模型。
        """
        if not self._should_generate_title(state):
            return None

        settings = self.title_settings
        user_msg = self._get_title_user_message(state)
        if not settings.model_name:
            return {"title": self._fallback_title(user_msg)}

        try:
            prompt = self._build_title_prompt(state)
            model = create_chat_model(settings.model_name, temperature=0)
            response = await model.ainvoke(
                prompt,
                config={
                    "metadata": {
                        "lc_source": _TITLE_SOURCE,
                        **internal_call_metadata(),
                    }
                },
            )
            title = self._parse_title(response.content)
            if title:
                return {"title": title}
        except Exception:
            logger.debug(
                "Failed to generate async title; falling back to local title",
                exc_info=True,
            )
        return {"title": self._fallback_title(user_msg)}

    @override
    def after_agent(
        self, state: TitleMiddlewareState, runtime: Runtime
    ) -> dict[str, Any] | None:
        """Generate a local fallback title after a synchronous run. / 在同步运行结束后生成本地后备标题。"""
        _ = runtime
        return self._generate_title_result(state)

    @override
    async def aafter_agent(
        self, state: TitleMiddlewareState, runtime: Runtime
    ) -> dict[str, Any] | None:
        """Generate the title after an asynchronous run. / 在异步运行结束后生成标题。"""
        _ = runtime
        return await self._agenerate_title_result(state)

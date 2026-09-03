"""Inject the current date into agent state. / 将当前日期注入智能体状态。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from html import escape
from typing import Any, Final
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

DYNAMIC_DATE_MARKER: Final = "zharness_dynamic_date"
REMINDER_DATE_KEY: Final = "reminder_date"
REMINDER_TIMEZONE_KEY: Final = "reminder_timezone"
INJECTED_USER_MESSAGE_ID_SUFFIX: Final = "__zharness_user"

_WEEKDAYS: Final = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def is_dynamic_date_reminder(message: AnyMessage) -> bool:
    """Return whether a message is a ZHarness date reminder. / 返回消息是否为 ZHarness 日期提醒。"""
    return isinstance(message, SystemMessage) and bool(
        message.additional_kwargs.get(DYNAMIC_DATE_MARKER)
    )


class DynamicDateMiddleware(AgentMiddleware):
    """Persist a hidden, timezone-aware current-date reminder.

    持久化一条隐藏且具备时区感知能力的当前日期提醒。
    """

    def __init__(
        self,
        timezone: str,
        *,
        now: Callable[[ZoneInfo], datetime] | None = None,
    ) -> None:
        """Initialize the middleware with an IANA timezone. / 使用 IANA 时区初始化中间件。"""
        super().__init__()
        try:
            self._timezone = ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown IANA timezone: {timezone!r}") from exc
        self._now = now or datetime.now

    @property
    def timezone(self) -> str:
        """Return the configured timezone name. / 返回已配置的时区名称。"""
        return self._timezone.key

    def _current_date(self) -> tuple[str, str]:
        """Return the stable machine and display forms of today's date. / 返回当日日期稳定的机器格式和展示格式。"""
        current = self._now(self._timezone)
        if current.tzinfo is None:
            current = current.replace(tzinfo=self._timezone)
        else:
            current = current.astimezone(self._timezone)
        date_value = current.date().isoformat()
        display_value = f"{date_value}, {_WEEKDAYS[current.weekday()]}"
        return date_value, display_value

    def _create_reminder(
        self,
        *,
        message_id: str,
        date_value: str,
        display_value: str,
    ) -> SystemMessage:
        """Create one hidden date reminder. / 创建一条隐藏日期提醒。"""
        timezone = escape(self.timezone, quote=True)
        return SystemMessage(
            id=message_id,
            content=(
                "<system-reminder>"
                f'<current_date timezone="{timezone}">{display_value}</current_date>'
                "</system-reminder>"
            ),
            additional_kwargs={
                "hide_from_ui": True,
                DYNAMIC_DATE_MARKER: True,
                REMINDER_DATE_KEY: date_value,
                REMINDER_TIMEZONE_KEY: self.timezone,
            },
        )

    @staticmethod
    def _existing_reminder(messages: Sequence[AnyMessage]) -> SystemMessage | None:
        """Find the most recent injected reminder. / 查找最近注入的日期提醒。"""
        for message in reversed(messages):
            if is_dynamic_date_reminder(message):
                return message
        return None

    @staticmethod
    def _injection_target(messages: Sequence[AnyMessage]) -> HumanMessage | None:
        """Find the latest user message that has not been moved. / 查找尚未移动的最新用户消息。"""
        for message in reversed(messages):
            if isinstance(message, HumanMessage) and not str(message.id or "").endswith(
                INJECTED_USER_MESSAGE_ID_SUFFIX
            ):
                return message
        return None

    def _build_update(self, messages: Sequence[AnyMessage]) -> dict[str, Any] | None:
        """Build a reducer-friendly state update for the current date. / 为当前日期构建适用于 reducer 的状态更新。"""
        if not messages:
            return None

        date_value, display_value = self._current_date()
        existing = self._existing_reminder(messages)
        if existing is not None:
            metadata = existing.additional_kwargs
            if (
                metadata.get(REMINDER_DATE_KEY) == date_value
                and metadata.get(REMINDER_TIMEZONE_KEY) == self.timezone
            ):
                return None
            reminder_id = str(existing.id or uuid4())
            return {
                "messages": [
                    self._create_reminder(
                        message_id=reminder_id,
                        date_value=date_value,
                        display_value=display_value,
                    )
                ]
            }

        target = self._injection_target(messages)
        if target is None:
            return None
        target_id = str(target.id or uuid4())
        moved_target = target.model_copy(
            update={"id": f"{target_id}{INJECTED_USER_MESSAGE_ID_SUFFIX}"}
        )
        return {
            "messages": [
                self._create_reminder(
                    message_id=target_id,
                    date_value=date_value,
                    display_value=display_value,
                ),
                moved_target,
            ]
        }

    def before_agent(
        self,
        state: AgentState,
        runtime: Runtime,
    ) -> dict[str, Any] | None:
        """Inject or refresh the reminder before a synchronous run. / 在同步运行前注入或刷新提醒。"""
        _ = runtime
        return self._build_update(state["messages"])

    async def abefore_agent(
        self,
        state: AgentState,
        runtime: Runtime,
    ) -> dict[str, Any] | None:
        """Inject or refresh the reminder before an asynchronous run. / 在异步运行前注入或刷新提醒。"""
        _ = runtime
        return self._build_update(state["messages"])

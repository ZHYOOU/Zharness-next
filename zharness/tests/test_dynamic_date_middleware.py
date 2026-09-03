"""Tests for dynamic date context injection. / 动态日期上下文注入测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph.message import add_messages
from zharness.middleware.dynamic_date import (
    DYNAMIC_DATE_MARKER,
    INJECTED_USER_MESSAGE_ID_SUFFIX,
    REMINDER_DATE_KEY,
    REMINDER_TIMEZONE_KEY,
    DynamicDateMiddleware,
)


class MutableClock:
    """Provide a controllable aware datetime. / 提供可控制的感知型日期时间。"""

    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self, timezone: ZoneInfo) -> datetime:
        return self.current.astimezone(timezone)


def test_first_run_injects_hidden_reminder_before_user_message() -> None:
    clock = MutableClock(datetime(2026, 9, 3, 4, tzinfo=ZoneInfo("UTC")))
    middleware = DynamicDateMiddleware("Asia/Shanghai", now=clock)
    original = HumanMessage(id="user-1", content="分析天气")

    update = middleware.before_agent({"messages": [original]}, None)  # type: ignore[arg-type]

    assert update is not None
    reminder, moved_user = update["messages"]
    assert isinstance(reminder, SystemMessage)
    assert reminder.id == "user-1"
    assert reminder.content == (
        '<system-reminder><current_date timezone="Asia/Shanghai">'
        "2026-09-03, Thursday</current_date></system-reminder>"
    )
    assert reminder.additional_kwargs == {
        "hide_from_ui": True,
        DYNAMIC_DATE_MARKER: True,
        REMINDER_DATE_KEY: "2026-09-03",
        REMINDER_TIMEZONE_KEY: "Asia/Shanghai",
    }
    assert isinstance(moved_user, HumanMessage)
    assert moved_user.id == f"user-1{INJECTED_USER_MESSAGE_ID_SUFFIX}"
    assert moved_user.content == original.content

    merged = add_messages([original], update["messages"])
    assert [type(message) for message in merged] == [SystemMessage, HumanMessage]


def test_same_day_does_not_duplicate_reminder() -> None:
    clock = MutableClock(datetime(2026, 9, 3, 4, tzinfo=ZoneInfo("UTC")))
    middleware = DynamicDateMiddleware("Asia/Shanghai", now=clock)
    original = HumanMessage(id="user-1", content="hello")
    first_update = middleware.before_agent(  # type: ignore[arg-type]
        {"messages": [original]}, None
    )
    assert first_update is not None
    messages = add_messages([original], first_update["messages"])

    assert middleware.before_agent({"messages": messages}, None) is None  # type: ignore[arg-type]


def test_crossing_midnight_replaces_existing_reminder() -> None:
    clock = MutableClock(datetime(2026, 9, 3, 15, tzinfo=ZoneInfo("UTC")))
    middleware = DynamicDateMiddleware("Asia/Shanghai", now=clock)
    original = HumanMessage(id="user-1", content="hello")
    first_update = middleware.before_agent(  # type: ignore[arg-type]
        {"messages": [original]}, None
    )
    assert first_update is not None
    messages = add_messages([original], first_update["messages"])

    clock.current = datetime(2026, 9, 3, 17, tzinfo=ZoneInfo("UTC"))
    refresh = middleware.before_agent({"messages": messages}, None)  # type: ignore[arg-type]

    assert refresh is not None
    assert len(refresh["messages"]) == 1
    refreshed = refresh["messages"][0]
    assert isinstance(refreshed, SystemMessage)
    assert refreshed.id == "user-1"
    assert refreshed.additional_kwargs[REMINDER_DATE_KEY] == "2026-09-04"
    assert "2026-09-04, Friday" in str(refreshed.content)
    merged = add_messages(messages, refresh["messages"])
    reminders = [
        message
        for message in merged
        if isinstance(message, SystemMessage)
        and message.additional_kwargs.get(DYNAMIC_DATE_MARKER)
    ]
    assert len(reminders) == 1


def test_async_hook_uses_the_same_update_logic() -> None:
    clock = MutableClock(datetime(2026, 9, 3, 17, tzinfo=ZoneInfo("UTC")))
    middleware = DynamicDateMiddleware("Asia/Shanghai", now=clock)
    original = HumanMessage(id="user-1", content="hello")

    update = asyncio.run(
        middleware.abefore_agent({"messages": [original]}, None)  # type: ignore[arg-type]
    )

    assert update is not None
    assert "2026-09-04, Friday" in str(update["messages"][0].content)


def test_invalid_timezone_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown IANA timezone"):
        DynamicDateMiddleware("Mars/Olympus_Mons")

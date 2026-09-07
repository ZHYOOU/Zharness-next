"""Tests for the automatic thread-title middleware. / 自动线程标题中间件的测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage, HumanMessage
from zharness.agents.lead import create_lead_agent
from zharness.config import get_settings
from zharness.middleware import title as title_module
from zharness.middleware.title import TitleMiddleware


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(execution_info=SimpleNamespace(thread_id="t1"))


def _middleware(**overrides) -> TitleMiddleware:
    settings = get_settings().title
    data = {
        "enabled": settings.enabled,
        "max_words": settings.max_words,
        "max_chars": settings.max_chars,
        "model_name": settings.model_name,
        "prompt_template": settings.prompt_template,
    }
    data.update(overrides)
    from zharness.config.settings import TitleSettings

    return TitleMiddleware(title_settings=TitleSettings(**data))


def test_should_generate_title_for_first_complete_exchange() -> None:
    middleware = _middleware(enabled=True)
    state = {
        "messages": [
            HumanMessage(content="帮我总结这段代码"),
            AIMessage(content="好的，我先看结构"),
        ]
    }

    assert middleware._should_generate_title(state) is True


def test_should_not_generate_when_disabled_or_already_set() -> None:
    disabled = _middleware(enabled=False)
    assert (
        disabled._should_generate_title(
            {"messages": [HumanMessage(content="Q"), AIMessage(content="A")]}
        )
        is False
    )

    titled = _middleware(enabled=True)
    assert (
        titled._should_generate_title(
            {
                "messages": [HumanMessage(content="Q"), AIMessage(content="A")],
                "title": "Existing",
            }
        )
        is False
    )


def test_should_not_generate_after_second_user_turn() -> None:
    middleware = _middleware(enabled=True)
    state = {
        "messages": [
            HumanMessage(content="第一问"),
            AIMessage(content="第一答"),
            HumanMessage(content="第二问"),
            AIMessage(content="第二答"),
        ]
    }

    assert middleware._should_generate_title(state) is False


def test_should_ignore_hidden_reminder_messages() -> None:
    middleware = _middleware(enabled=True)
    state = {
        "messages": [
            HumanMessage(
                content="<system-reminder>hidden</system-reminder>",
                additional_kwargs={"hide_from_ui": True},
            ),
            HumanMessage(content="请帮我写测试"),
            AIMessage(content="好的"),
        ]
    }

    assert middleware._should_generate_title(state) is True
    assert middleware._get_title_user_message(state) == "请帮我写测试"


def test_sync_after_agent_uses_local_fallback() -> None:
    middleware = _middleware(enabled=True, max_chars=20)
    state = {
        "messages": [
            HumanMessage(content="请帮我写测试"),
            AIMessage(content="好的"),
        ]
    }

    assert middleware.after_agent(state, _runtime()) == {"title": "请帮我写测试"}


def test_sync_after_agent_returns_none_until_complete() -> None:
    middleware = _middleware(enabled=True, max_chars=20)
    state = {"messages": [HumanMessage(content="请帮我写测试")]}

    assert middleware.after_agent(state, _runtime()) is None


def test_async_after_agent_fallback_skips_title_model(monkeypatch) -> None:
    middleware = _middleware(enabled=True, max_chars=20, model_name=None)
    create_chat_model = MagicMock()
    monkeypatch.setattr(title_module, "create_chat_model", create_chat_model)

    state = {
        "messages": [
            HumanMessage(content="请帮我写测试"),
            AIMessage(content="好的"),
        ]
    }
    result = asyncio_run(middleware.aafter_agent(state, _runtime()))

    assert result == {"title": "请帮我写测试"}
    create_chat_model.assert_not_called()


def test_async_after_agent_invokes_title_model(monkeypatch) -> None:
    middleware = _middleware(enabled=True, max_chars=20, model_name="title-model")
    model = MagicMock()
    model.ainvoke = AsyncMock(return_value=AIMessage(content="短标题"))
    create_chat_model = MagicMock(return_value=model)
    monkeypatch.setattr(title_module, "create_chat_model", create_chat_model)

    state = {
        "messages": [
            HumanMessage(content="请帮我写一个标题"),
            AIMessage(content="好的"),
        ]
    }
    result = asyncio_run(middleware.aafter_agent(state, _runtime()))

    assert result == {"title": "短标题"}
    create_chat_model.assert_called_once_with("title-model", temperature=0)
    model.ainvoke.assert_awaited_once()


def test_async_after_agent_falls_back_on_model_error(monkeypatch) -> None:
    middleware = _middleware(enabled=True, max_chars=20, model_name="title-model")
    model = MagicMock()
    model.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(
        title_module, "create_chat_model", MagicMock(return_value=model)
    )

    state = {
        "messages": [
            HumanMessage(
                content="这是一个非常长的问题描述，需要被截断以形成fallback标题"
            ),
            AIMessage(content="收到"),
        ]
    }
    result = asyncio_run(middleware.aafter_agent(state, _runtime()))

    assert result is not None
    assert result["title"].endswith("...")
    assert result["title"].startswith("这是一个非常长的问题描述")


def test_parse_title_strips_think_tags() -> None:
    middleware = _middleware(max_chars=60)
    raw = (
        " thinking用户想要研究贵阳发展情况。我需要使用 deep-research skill。"
        " response贵阳近5年发展报告研究"
    )
    assert middleware._parse_title(raw) == "贵阳近5年发展报告研究"


@pytest.mark.parametrize("max_chars", [10, 20, 40, 49, 50, 60, 200])
def test_fallback_title_never_exceeds_max_chars(max_chars: int) -> None:
    middleware = _middleware(max_chars=max_chars, model_name=None)
    title = middleware._fallback_title("x" * 200)
    assert len(title) <= max_chars
    assert title.endswith("...")


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


class ToolCallingFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        _ = tools, tool_choice, kwargs
        return self


def test_lead_agent_writes_title_after_first_turn(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ZHARNESS_SKILLS_PATH", str(tmp_path / "no-skills"))
    monkeypatch.setenv("ZHARNESS_TITLE_MAX_CHARS", "60")
    model = ToolCallingFakeModel(responses=[AIMessage(content="hello")])

    agent = create_lead_agent(model)

    assert "TitleMiddleware.after_agent" in agent.nodes

    result = agent.invoke({"messages": [{"role": "user", "content": "帮我研究贵阳"}]})

    assert result["title"] == "帮我研究贵阳"


def test_lead_agent_title_channel_is_optional_before_first_turn(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("ZHARNESS_SKILLS_PATH", str(tmp_path / "no-skills"))
    model = ToolCallingFakeModel(responses=[AIMessage(content="hello")])

    agent = create_lead_agent(model)

    result = agent.invoke({"messages": [{"role": "user", "content": "帮我研究贵阳"}]})

    assert "title" in result

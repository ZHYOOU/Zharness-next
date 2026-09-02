import pytest
from langchain_anthropic import ChatAnthropic
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI
from zharness.models.factory import (
    DEFAULT_MIMO_BASE_URL,
    PROVIDER_ANTHROPIC,
    PROVIDER_DEEPSEEK,
    PROVIDER_MIMO,
    PROVIDER_OPENAI,
    create_chat_model,
    resolve_provider,
)


def _clear_provider(monkeypatch) -> None:
    """Clear the provider override and all API keys. / 清除提供商覆盖与所有 API Key。"""
    monkeypatch.delenv("ZHARNESS_MODEL_PROVIDER", raising=False)
    for key in (
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "ANTHROPIC_API_KEY",
        "MIMO_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


def test_resolve_provider_prefers_environment(monkeypatch) -> None:
    monkeypatch.setenv("ZHARNESS_MODEL_PROVIDER", "anthropic")
    assert resolve_provider("deepseek-chat") == PROVIDER_ANTHROPIC


def test_resolve_provider_infers_from_model_name(monkeypatch) -> None:
    _clear_provider(monkeypatch)
    assert resolve_provider("claude-sonnet-4-5") == PROVIDER_ANTHROPIC
    assert resolve_provider("deepseek-chat") == PROVIDER_DEEPSEEK
    assert resolve_provider("mimo-v2.5") == PROVIDER_MIMO
    assert resolve_provider("gpt-5") == PROVIDER_OPENAI


def test_create_chat_model_mimo(monkeypatch) -> None:
    _clear_provider(monkeypatch)
    monkeypatch.setenv("MIMO_API_KEY", "sk-test")

    model = create_chat_model("mimo-v2.5")

    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "mimo-v2.5"
    assert model.openai_api_base == DEFAULT_MIMO_BASE_URL
    assert model.openai_api_key.get_secret_value() == "sk-test"
    assert model.temperature == 0
    assert model.request_timeout == 60
    assert model.max_retries == 3


def test_create_chat_model_mimo_base_url_override(monkeypatch) -> None:
    _clear_provider(monkeypatch)
    monkeypatch.setenv("MIMO_API_KEY", "tp-test")
    monkeypatch.setenv(
        "ZHARNESS_MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1"
    )

    model = create_chat_model("mimo-v2.5")

    assert model.openai_api_base == "https://token-plan-cn.xiaomimimo.com/v1"


def test_create_chat_model_openai(monkeypatch) -> None:
    _clear_provider(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    model = create_chat_model("gpt-4o")

    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "gpt-4o"
    assert model.temperature == 0
    assert model.request_timeout == 60
    assert model.max_retries == 3


def test_create_chat_model_openai_compatible_base_url(monkeypatch) -> None:
    _clear_provider(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ZHARNESS_OPENAI_BASE_URL", "https://ollama.example/v1")

    model = create_chat_model("qwen3")

    assert isinstance(model, ChatOpenAI)
    assert model.openai_api_base == "https://ollama.example/v1"


def test_create_chat_model_deepseek(monkeypatch) -> None:
    _clear_provider(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    model = create_chat_model("deepseek-chat")

    assert isinstance(model, ChatDeepSeek)
    assert model.model_name == "deepseek-chat"
    assert model.temperature == 0


def test_create_chat_model_anthropic(monkeypatch) -> None:
    _clear_provider(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    model = create_chat_model("claude-sonnet-4-5")

    assert isinstance(model, ChatAnthropic)
    assert model.model == "claude-sonnet-4-5"
    assert model.default_request_timeout == 60
    assert model.max_retries == 3


def test_create_chat_model_rejects_unknown_provider(monkeypatch) -> None:
    monkeypatch.setenv("ZHARNESS_MODEL_PROVIDER", "gemini")
    with pytest.raises(ValueError, match="Unsupported ZHARNESS_MODEL_PROVIDER"):
        create_chat_model("gemini-2.5-pro")

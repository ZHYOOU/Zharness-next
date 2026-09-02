"""Multi-provider chat model factory.

Creates a chat model for a configured provider: ``mimo`` (default), ``deepseek``,
``openai`` (including OpenAI-compatible endpoints), or ``anthropic``. The
provider is read from ``ZHARNESS_MODEL_PROVIDER`` (or the ``model.provider``
YAML setting) and inferred from the model name when unset. API keys are read
from the provider-standard environment variables (``MIMO_API_KEY``,
``DEEPSEEK_API_KEY``, ``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``).

多提供商聊天模型工厂。为已配置的提供商创建聊天模型：``mimo``（默认）、``deepseek``、``openai``（含 OpenAI 兼容端点）或 ``anthropic``。
提供商从 ``ZHARNESS_MODEL_PROVIDER``（或 ``model.provider`` YAML 配置）读取，未设置时根据模型名推断。API Key 从提供商标准环境变量读取
（``MIMO_API_KEY``、``DEEPSEEK_API_KEY``、``OPENAI_API_KEY``、``ANTHROPIC_API_KEY``）。
"""

from __future__ import annotations

import os

from langchain_core.language_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

from zharness.config.loader import get_settings

PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_OPENAI = "openai"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_MIMO = "mimo"

SUPPORTED_PROVIDERS = (
    PROVIDER_MIMO,
    PROVIDER_DEEPSEEK,
    PROVIDER_OPENAI,
    PROVIDER_ANTHROPIC,
)
"""Supported provider names. / 支持的提供商名称。"""

_PROVIDER_BY_MODEL_PREFIX: tuple[tuple[str, str], ...] = (
    ("mimo", PROVIDER_MIMO),
    ("claude", PROVIDER_ANTHROPIC),
    ("deepseek", PROVIDER_DEEPSEEK),
)
"""Model-name prefixes mapped to their provider, checked in order. / 按顺序检查的模型名前缀到提供商的映射。"""

DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MAX_RETRIES = 3
DEFAULT_MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"


def resolve_provider(model_name: str) -> str:
    """Resolve the provider for *model_name*.

    Prefers ``ZHARNESS_MODEL_PROVIDER`` (or the ``model.provider`` YAML
    setting); falls back to inferring the provider from the model-name prefix
    (``mimo`` → MiMo, ``claude`` → anthropic, ``deepseek`` → deepseek),
    defaulting to OpenAI for everything else.

    为 *model_name* 解析提供商。优先使用 ``ZHARNESS_MODEL_PROVIDER``（或 ``model.provider`` YAML 配置）；
    否则根据模型名前缀推断（``mimo`` → MiMo，``claude`` → anthropic，``deepseek`` → deepseek），其余默认使用 OpenAI。
    """
    configured = get_settings().model.provider
    if configured:
        return configured.strip().lower()
    lowered = model_name.lower()
    for prefix, provider in _PROVIDER_BY_MODEL_PREFIX:
        if lowered.startswith(prefix):
            return provider
    return PROVIDER_OPENAI


def create_chat_model(model_name: str, *, temperature: float = 0) -> BaseChatModel:
    """Create a chat model for *model_name* on its resolved provider.

    The provider is ``ZHARNESS_MODEL_PROVIDER`` or inferred from the model
    name. MiMo uses ``MIMO_API_KEY`` and its OpenAI-compatible endpoint;
    other OpenAI-compatible endpoints can be set with
    ``ZHARNESS_OPENAI_BASE_URL`` or the ``model.openai_base_url`` YAML setting.

    为 *model_name* 在其解析出的提供商上创建聊天模型。提供商为 ``ZHARNESS_MODEL_PROVIDER`` 或根据模型名推断。
    MiMo 使用 ``MIMO_API_KEY`` 和其 OpenAI 兼容端点；其他 OpenAI 兼容端点可通过
    ``ZHARNESS_OPENAI_BASE_URL`` 或 ``model.openai_base_url`` YAML 配置设置。
    """
    settings = get_settings()
    provider = resolve_provider(model_name)
    if provider == PROVIDER_MIMO:
        return ChatOpenAI(
            model=model_name,
            temperature=temperature,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            max_retries=DEFAULT_MAX_RETRIES,
            api_key=os.environ.get("MIMO_API_KEY"),
            base_url=settings.model.mimo_base_url or DEFAULT_MIMO_BASE_URL,
        )
    if provider == PROVIDER_DEEPSEEK:
        return ChatDeepSeek(
            model=model_name,
            temperature=temperature,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            max_retries=DEFAULT_MAX_RETRIES,
        )
    if provider == PROVIDER_OPENAI:
        return ChatOpenAI(
            model=model_name,
            temperature=temperature,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            max_retries=DEFAULT_MAX_RETRIES,
            base_url=settings.model.openai_base_url,
        )
    if provider == PROVIDER_ANTHROPIC:
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model_name,
            temperature=temperature,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            max_retries=DEFAULT_MAX_RETRIES,
            base_url=settings.model.anthropic_base_url,
        )
    raise ValueError(
        f"Unsupported ZHARNESS_MODEL_PROVIDER {provider!r}; expected one of "
        f"{', '.join(SUPPORTED_PROVIDERS)}"
    )

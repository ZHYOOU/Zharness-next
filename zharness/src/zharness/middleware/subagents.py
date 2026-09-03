"""Subagent delegation middleware for ZHarness. / ZHarness 的子智能体委派中间件。"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import threading
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from typing import Any, Literal, NotRequired, TypedDict, cast

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware, InterruptOnConfig
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ContextT,
    ModelRequest,
    ModelResponse,
    ResponseT,
)
from langchain.agents.structured_output import ResponseFormat
from langchain.tools import BaseTool, ToolRuntime
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.types import Command
from pydantic import BaseModel, Field

DEFAULT_SUBAGENT_PROMPT = """Complete the delegated objective autonomously using the tools available to you.

The calling agent sees only your final assistant message, not your intermediate work or tool results. Return a complete, concise report that the calling agent can use directly."""

DEFAULT_GENERAL_PURPOSE_DESCRIPTION = (
    "A general-purpose agent for exploring code, researching complex questions, "
    "and completing self-contained multi-step tasks."
)

TASK_TOOL_DESCRIPTION = """Launch an ephemeral subagent for a self-contained, multi-step task.

Available subagent types:
{available_agents}

Choose a subagent with `subagent_type` and give it all necessary context in `description`.
Independent tasks may be launched concurrently with multiple tool calls. Each invocation is
isolated unless its agent type explicitly says that it inherits the conversation. The subagent's
report is returned to you; synthesize or relay it to the user.

{limits}"""

DEFAULT_SUBAGENT_TIMEOUT_SECONDS = 900
"""Default wall-clock limit for one subagent run. / 单个子智能体运行的默认墙钟时长上限。"""

DEFAULT_MAX_CONCURRENT_SUBAGENTS = 3
"""Default number of subagents allowed to run at the same time. / 默认允许同时运行的子智能体数量。"""

_TAG_PATTERN = re.compile(r"</?[a-zA-Z][a-zA-Z0-9_-]*(?:\s+[^<>]*)?>")


class SubagentTimeoutError(Exception):
    """Raised when a subagent exceeds its configured timeout. / 子智能体超过其配置超时时抛出。"""

    def __init__(self, timeout: float) -> None:
        super().__init__(f"Subagent exceeded its {timeout:g}-second timeout")
        self.timeout = timeout


def _sanitize_untrusted(text: str) -> str:
    """Escape tag-like markup in untrusted input to blunt prompt injection.

    对不可信输入中的类标签标记进行转义，以削弱提示注入。
    """
    return _TAG_PATTERN.sub(
        lambda match: match.group(0).replace("<", "&lt;").replace(">", "&gt;"),
        text,
    )


async def _ainvoke_with_timeout(
    coro: Awaitable[Any],
    timeout: float | None,
) -> Any:
    """Await a subagent run, enforcing an optional wall-clock timeout. / 等待子智能体运行，并施加可选的墙钟超时。"""
    try:
        if timeout is None:
            return await coro
        return await asyncio.wait_for(coro, timeout=timeout)
    except TimeoutError as exc:
        raise SubagentTimeoutError(timeout) from exc


def _run_sync_with_timeout(
    runnable: Runnable,
    state: dict[str, Any],
    config: RunnableConfig,
    timeout: float,
) -> Any:
    """Run a subagent synchronously while applying a timeout via a fresh event loop.

    The subagent runs in the current thread so thread-scoped state and
    context variables are preserved; a fresh event loop enables cancellation.

    在当前线程中同步运行子智能体以保留线程作用域状态与上下文变量；
    通过新建事件循环实现超时取消。
    """

    async def run() -> Any:
        return await _ainvoke_with_timeout(runnable.ainvoke(state, config), timeout)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(run())
    # A fresh loop is impossible while a loop runs in this thread; fall back to
    # an untimed sync run rather than misbehave. / 当前线程已有运行中的事件循环，
    # 无法新建循环；回退为不受限的同步运行以免行为异常。
    return runnable.invoke(state, config)


def _timeout_command(
    subagent_type: str,
    timeout: float,
    tool_call_id: str,
) -> Command:
    """Build a ToolMessage that tells the parent a subagent timed out. / 构造告知父智能体子智能体已超时的 ToolMessage。"""
    content = (
        f"Subagent {subagent_type!r} did not finish within its {timeout:g}-second "
        "timeout and was cancelled. Its work may be incomplete; retry with a "
        "smaller task or continue without it."
    )
    return Command(
        update={"messages": [ToolMessage(content=content, tool_call_id=tool_call_id)]}
    )


_EXCLUDED_STATE_KEYS = frozenset({"messages", "todos", "structured_response"})
_SUBAGENT_DEPTH: ContextVar[int] = ContextVar("zharness_subagent_depth", default=0)
_RECURSION_REFUSAL = (
    "You are already running as a subagent and cannot delegate to another "
    "subagent. Complete the task yourself."
)


class SubAgent(TypedDict):
    """Describe a subagent that the middleware compiles on initialization.

    描述一个由中间件在初始化时编译的子智能体。
    """

    name: str
    description: str
    system_prompt: NotRequired[str]
    tools: NotRequired[Sequence[BaseTool | Callable[..., Any] | dict[str, Any]]]
    model: NotRequired[str | BaseChatModel]
    middleware: NotRequired[list[AgentMiddleware]]
    interrupt_on: NotRequired[dict[str, bool | InterruptOnConfig]]
    response_format: NotRequired[ResponseFormat[Any] | type | dict[str, Any]]
    mode: NotRequired[Literal["handoff", "fork"]]
    timeout_seconds: NotRequired[float]
    """Wall-clock limit for one run; overrides the middleware default. / 单次运行的墙钟时长上限；覆盖中间件默认值。"""


class CompiledSubAgent(TypedDict):
    """Describe a precompiled runnable used as a subagent. / 描述用作子智能体的预编译 Runnable。"""

    name: str
    description: str
    runnable: Runnable
    mode: NotRequired[Literal["handoff", "fork"]]


SubAgentSpec = SubAgent | CompiledSubAgent


class TaskToolSchema(BaseModel):
    """Validate arguments accepted by the task tool. / 校验 task 工具接收的参数。"""

    description: str = Field(
        description="A detailed task description containing all required context and the expected output."
    )
    subagent_type: str = Field(
        description="The name of one of the subagent types listed in this tool's description."
    )


def _append_system_message(
    system_message: SystemMessage | None, text: str
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


def _validate_subagents(subagents: Sequence[SubAgentSpec]) -> None:
    """Validate names, modes, and declarative requirements. / 校验名称、模式和声明式配置要求。"""
    if not subagents:
        raise ValueError("At least one subagent must be specified")
    seen: set[str] = set()
    for spec in subagents:
        name = spec["name"]
        if not name.strip():
            raise ValueError("Subagent names must not be empty")
        if name in seen:
            raise ValueError(
                f"Duplicate subagent name {name!r}; each subagent must have a unique name"
            )
        seen.add(name)
        mode = spec.get("mode", "handoff")
        if mode not in ("handoff", "fork"):
            raise ValueError(
                f"Subagent {name!r} has invalid mode {mode!r}; expected 'handoff' or 'fork'"
            )
        if "runnable" not in spec:
            if "model" not in spec:
                raise ValueError(f"Subagent {name!r} must specify 'model'")
            if "tools" not in spec:
                raise ValueError(f"Subagent {name!r} must specify 'tools'")
        if (timeout := spec.get("timeout_seconds")) is not None and timeout <= 0:
            raise ValueError(
                f"Subagent {name!r} has invalid timeout_seconds {timeout!r}; "
                "expected a positive number"
            )


def create_sub_agent(spec: SubAgent) -> Runnable:
    """Compile a declarative subagent specification. / 编译声明式子智能体配置。"""
    if "model" not in spec:
        raise ValueError(f"Subagent {spec['name']!r} must specify 'model'")
    if "tools" not in spec:
        raise ValueError(f"Subagent {spec['name']!r} must specify 'tools'")
    middleware = list(spec.get("middleware", []))
    if interrupt_on := spec.get("interrupt_on"):
        middleware.append(HumanInTheLoopMiddleware(interrupt_on=interrupt_on))
    return create_agent(
        name=spec["name"],
        model=spec["model"],
        tools=spec["tools"],
        middleware=middleware,
        system_prompt=spec.get("system_prompt", ""),
        response_format=spec.get("response_format"),
    )


def _serialize_structured_response(value: Any) -> str:
    """Serialize a structured subagent response as JSON. / 将子智能体的结构化响应序列化为 JSON。"""
    if hasattr(value, "model_dump_json"):
        return cast("Any", value).model_dump_json()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = dataclasses.asdict(value)
    return json.dumps(value, ensure_ascii=False)


def _last_ai_text(messages: Sequence[Any]) -> str:
    """Return the last non-empty assistant text. / 返回最后一条非空助手文本。"""
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            text = message.text.rstrip() if message.text else ""
            if text:
                return text
    return ""


def _result_command(
    result: dict[str, Any], tool_call_id: str, private_state_keys: frozenset[str]
) -> Command:
    """Convert subagent state into a parent graph update. / 将子智能体状态转换为父图更新。"""
    if "messages" not in result:
        raise ValueError(
            "A compiled subagent must return state containing a 'messages' key"
        )
    structured = result.get("structured_response")
    content = (
        _serialize_structured_response(structured)
        if structured is not None
        else _last_ai_text(result["messages"])
    )
    state_update = {
        key: value
        for key, value in result.items()
        if key not in _EXCLUDED_STATE_KEYS and key not in private_state_keys
    }
    return Command(
        update={
            **state_update,
            "messages": [ToolMessage(content=content, tool_call_id=tool_call_id)],
        }
    )


def _build_task_tool(
    subagents: Sequence[SubAgentSpec],
    task_description: str | None = None,
    *,
    private_state_keys: frozenset[str] = frozenset(),
    timeout_seconds: float | None = DEFAULT_SUBAGENT_TIMEOUT_SECONDS,
    max_concurrent_subagents: int | None = DEFAULT_MAX_CONCURRENT_SUBAGENTS,
    sanitize_input: bool = True,
) -> BaseTool:
    """Build the task tool that dispatches to named subagents. / 构建按名字调度子智能体的 task 工具。"""
    _validate_subagents(subagents)
    descriptions = "\n".join(
        f"- {spec['name']}: {spec['description']}"
        + (" (inherits the parent conversation)" if spec.get("mode") == "fork" else "")
        for spec in subagents
    )

    limits: list[str] = []
    if max_concurrent_subagents is not None:
        limits.append(
            f"Up to {max_concurrent_subagents} subagents may run at the same time; "
            "additional calls wait for a free slot."
        )
    if timeout_seconds is not None:
        limits.append(
            "Subagents must finish within their timeout; a timed-out run is "
            "cancelled and reported back to you."
        )
    limits_section = "\n".join(limits)

    if task_description is None:
        description = TASK_TOOL_DESCRIPTION.format(
            available_agents=descriptions,
            limits=limits_section,
        )
    elif "{available_agents}" in task_description or "{limits}" in task_description:
        description = task_description.format(
            available_agents=descriptions,
            limits=limits_section,
        )
    else:
        description = task_description

    specs_by_name = {spec["name"]: spec for spec in subagents}
    runnables = {
        spec["name"]: (
            spec["runnable"].with_config(
                {"run_name": spec["name"], "metadata": {"lc_agent_name": spec["name"]}}
            )
            if "runnable" in spec
            else create_sub_agent(spec)
        )
        for spec in subagents
    }

    async_semaphore: asyncio.Semaphore | None
    sync_semaphore: threading.BoundedSemaphore | None
    if max_concurrent_subagents is None:
        async_semaphore = None
        sync_semaphore = None
    else:
        async_semaphore = asyncio.Semaphore(max_concurrent_subagents)
        sync_semaphore = threading.BoundedSemaphore(max_concurrent_subagents)

    def effective_timeout(subagent_type: str) -> float | None:
        """Return the per-agent timeout or the middleware default. / 返回子智能体级超时或中间件默认值。"""
        agent_timeout = specs_by_name[subagent_type].get("timeout_seconds")
        return agent_timeout if agent_timeout is not None else timeout_seconds

    def prepare_state(
        selected: SubAgentSpec, delegated_description: str, runtime: ToolRuntime
    ) -> dict[str, Any]:
        """Prepare isolated or forked state for one invocation. / 为一次调用准备隔离或分叉状态。"""
        if sanitize_input:
            delegated_description = _sanitize_untrusted(delegated_description)
        inherited = {
            key: value
            for key, value in runtime.state.items()
            if key not in _EXCLUDED_STATE_KEYS and key not in private_state_keys
        }
        if selected.get("mode") == "fork":
            messages = list(runtime.state.get("messages", []))
            if (
                messages
                and isinstance(messages[-1], AIMessage)
                and messages[-1].tool_calls
            ):
                messages.pop()
            inherited["messages"] = [
                *messages,
                HumanMessage(content=delegated_description),
            ]
        else:
            inherited["messages"] = [HumanMessage(content=delegated_description)]
        return inherited

    def invalid_type_message(subagent_type: str) -> str:
        """Return a model-readable invalid agent error. / 返回模型可读的无效智能体错误。"""
        allowed = ", ".join(f"`{name}`" for name in runnables)
        return f"Cannot invoke subagent {subagent_type!r}; it does not exist. Allowed types: {allowed}."

    def task(
        description: str, subagent_type: str, runtime: ToolRuntime
    ) -> str | Command:
        """Invoke one subagent synchronously. / 同步调用一个子智能体。"""
        if _SUBAGENT_DEPTH.get() > 0:
            return _RECURSION_REFUSAL
        if subagent_type not in runnables:
            return invalid_type_message(subagent_type)
        if not runtime.tool_call_id:
            raise ValueError("Tool call ID is required for subagent invocation")
        state = prepare_state(specs_by_name[subagent_type], description, runtime)
        config: RunnableConfig = {"configurable": {"ls_agent_type": "subagent"}}
        timeout = effective_timeout(subagent_type)
        depth_token = _SUBAGENT_DEPTH.set(_SUBAGENT_DEPTH.get() + 1)
        try:
            if sync_semaphore is not None:
                sync_semaphore.acquire()
            try:
                try:
                    if timeout is None:
                        result = runnables[subagent_type].invoke(state, config)
                    else:
                        result = _run_sync_with_timeout(
                            runnables[subagent_type], state, config, timeout
                        )
                except SubagentTimeoutError as exc:
                    return _timeout_command(
                        subagent_type, exc.timeout, runtime.tool_call_id
                    )
            finally:
                if sync_semaphore is not None:
                    sync_semaphore.release()
        finally:
            _SUBAGENT_DEPTH.reset(depth_token)
        return _result_command(result, runtime.tool_call_id, private_state_keys)

    async def atask(
        description: str, subagent_type: str, runtime: ToolRuntime
    ) -> str | Command:
        """Invoke one subagent asynchronously. / 异步调用一个子智能体。"""
        if _SUBAGENT_DEPTH.get() > 0:
            return _RECURSION_REFUSAL
        if subagent_type not in runnables:
            return invalid_type_message(subagent_type)
        if not runtime.tool_call_id:
            raise ValueError("Tool call ID is required for subagent invocation")
        state = prepare_state(specs_by_name[subagent_type], description, runtime)
        config: RunnableConfig = {"configurable": {"ls_agent_type": "subagent"}}
        timeout = effective_timeout(subagent_type)
        depth_token = _SUBAGENT_DEPTH.set(_SUBAGENT_DEPTH.get() + 1)
        try:
            if async_semaphore is not None:
                await async_semaphore.acquire()
            try:
                try:
                    result = await _ainvoke_with_timeout(
                        runnables[subagent_type].ainvoke(state, config), timeout
                    )
                except SubagentTimeoutError as exc:
                    return _timeout_command(
                        subagent_type, exc.timeout, runtime.tool_call_id
                    )
            finally:
                if async_semaphore is not None:
                    async_semaphore.release()
        finally:
            _SUBAGENT_DEPTH.reset(depth_token)
        return _result_command(result, runtime.tool_call_id, private_state_keys)

    return StructuredTool.from_function(
        name="task",
        func=task,
        coroutine=atask,
        description=description,
        infer_schema=False,
        args_schema=TaskToolSchema,
    )


class SubAgentMiddleware(AgentMiddleware[Any, ContextT, ResponseT]):
    """Expose configured subagents to a parent agent through a task tool.

    通过 task 工具向父智能体开放已配置的子智能体。
    """

    def __init__(
        self,
        *,
        subagents: Sequence[SubAgentSpec],
        system_prompt: str | None = None,
        task_description: str | None = None,
        private_state_keys: frozenset[str] | None = None,
        timeout_seconds: float | None = DEFAULT_SUBAGENT_TIMEOUT_SECONDS,
        max_concurrent_subagents: int | None = DEFAULT_MAX_CONCURRENT_SUBAGENTS,
        sanitize_input: bool = True,
    ) -> None:
        """Initialize subagent delegation. / 初始化子智能体委派功能。"""
        super().__init__()
        if max_concurrent_subagents is not None and max_concurrent_subagents < 1:
            raise ValueError(
                f"max_concurrent_subagents must be a positive integer, got "
                f"{max_concurrent_subagents!r}"
            )
        self._subagents = tuple(subagents)
        self._task_description = task_description
        self._private_state_keys = private_state_keys or frozenset()
        _validate_subagents(self._subagents)
        self.subagent_names = frozenset(spec["name"] for spec in self._subagents)
        self.system_prompt = system_prompt
        self.tools = [
            _build_task_tool(
                self._subagents,
                task_description,
                private_state_keys=self._private_state_keys,
                timeout_seconds=timeout_seconds,
                max_concurrent_subagents=max_concurrent_subagents,
                sanitize_input=sanitize_input,
            )
        ]

    @property
    def private_state_keys(self) -> frozenset[str]:
        """Return state keys hidden from subagents. / 返回对子智能体隐藏的状态键。"""
        return self._private_state_keys

    @private_state_keys.setter
    def private_state_keys(self, value: frozenset[str]) -> None:
        """Update hidden state keys and rebuild the task tool. / 更新隐藏状态键并重建 task 工具。"""
        self._private_state_keys = value
        self.tools = [
            _build_task_tool(
                self._subagents,
                self._task_description,
                private_state_keys=value,
            )
        ]

    def _render_system_prompt(self) -> str:
        """Render guidance with the available agent list. / 渲染包含可用智能体列表的说明。"""
        agents = "\n".join(
            f"- {spec['name']}: {spec['description']}" for spec in self._subagents
        )
        return f"{self.system_prompt}\n\nAvailable subagent types:\n{agents}"

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        """Add delegation guidance to synchronous model calls. / 为同步模型调用添加委派说明。"""
        if self.system_prompt:
            request = request.override(
                system_message=_append_system_message(
                    request.system_message, self._render_system_prompt()
                )
            )
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[
            [ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]
        ],
    ) -> ModelResponse[ResponseT]:
        """Add delegation guidance to asynchronous model calls. / 为异步模型调用添加委派说明。"""
        if self.system_prompt:
            request = request.override(
                system_message=_append_system_message(
                    request.system_message, self._render_system_prompt()
                )
            )
        return await handler(request)


GENERAL_PURPOSE_SUBAGENT: SubAgent = {
    "name": "general-purpose",
    "description": DEFAULT_GENERAL_PURPOSE_DESCRIPTION,
    "system_prompt": DEFAULT_SUBAGENT_PROMPT,
}
"""Base general-purpose spec; callers provide its model and tools. / 通用子智能体基础配置；调用方提供模型和工具。"""


__all__ = [
    "DEFAULT_GENERAL_PURPOSE_DESCRIPTION",
    "DEFAULT_MAX_CONCURRENT_SUBAGENTS",
    "DEFAULT_SUBAGENT_PROMPT",
    "DEFAULT_SUBAGENT_TIMEOUT_SECONDS",
    "GENERAL_PURPOSE_SUBAGENT",
    "TASK_TOOL_DESCRIPTION",
    "CompiledSubAgent",
    "SubAgent",
    "SubAgentMiddleware",
    "SubAgentSpec",
    "TaskToolSchema",
    "create_sub_agent",
]

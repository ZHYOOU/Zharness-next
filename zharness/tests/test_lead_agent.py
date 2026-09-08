from types import SimpleNamespace

from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from zharness.agents.lead import SYSTEM_PROMPT, create_lead_agent
from zharness.middleware.dynamic_date import DYNAMIC_DATE_MARKER
from zharness.sandbox.protocol import ReadResult
from zharness.tools import workspace as workspace_module


class ToolCallingFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        _ = tools, tool_choice, kwargs
        return self


def test_system_prompt_requires_bounded_network_requests_and_shared_workspace() -> None:
    assert "urlopen(..., timeout=15)" in SYSTEM_PROMPT
    assert "curl --max-time 15" in SYSTEM_PROMPT
    assert "`/workspace` is the stable path" in SYSTEM_PROMPT


def test_create_lead_agent(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ZHARNESS_SKILLS_PATH", str(tmp_path / "no-skills"))
    model = ToolCallingFakeModel(responses=[AIMessage(content="hello")])

    agent = create_lead_agent(model)

    assert agent.name == "lead_agent"
    assert agent.context_schema is None
    assert "DynamicDateMiddleware.before_agent" in agent.nodes
    assert "SummarizationMiddleware.before_model" in agent.nodes
    assert "HumanInTheLoopMiddleware.after_model" in agent.nodes
    assert "TokenUsageMiddleware.after_model" in agent.nodes
    assert set(agent.nodes["tools"].bound.tools_by_name) == {
        "write_todos",
        "list_workspace",
        "read_file",
        "write_file",
        "edit_file",
        "delete_path",
        "glob_files",
        "grep_files",
        "execute_command",
        "web_search",
        "task",
        "memory_search",
        "memory_add",
        "memory_update",
        "memory_delete",
        "knowledge_search",
        "knowledge_ingest",
        "knowledge_list",
        "knowledge_delete",
    }


def test_create_lead_agent_omits_memory_tools_when_disabled(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("ZHARNESS_SKILLS_PATH", str(tmp_path / "no-skills"))
    monkeypatch.setenv("ZHARNESS_MEMORY_ENABLED", "false")
    model = ToolCallingFakeModel(responses=[AIMessage(content="hello")])

    agent = create_lead_agent(model)

    assert "memory_search" not in agent.nodes["tools"].bound.tools_by_name
    assert "memory_add" not in agent.nodes["tools"].bound.tools_by_name


def test_create_lead_agent_omits_token_usage_middleware_when_disabled(
    tmp_path, monkeypatch
) -> None:
    """Honor the token usage master switch. / 遵循 token 用量总开关。"""
    monkeypatch.setenv("ZHARNESS_SKILLS_PATH", str(tmp_path / "no-skills"))
    monkeypatch.setenv("ZHARNESS_TOKEN_USAGE_ENABLED", "false")
    model = ToolCallingFakeModel(responses=[AIMessage(content="hello")])

    agent = create_lead_agent(model)

    assert "TokenUsageMiddleware.after_model" not in agent.nodes


def test_create_lead_agent_omits_knowledge_tools_when_disabled(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("ZHARNESS_SKILLS_PATH", str(tmp_path / "no-skills"))
    monkeypatch.setenv("ZHARNESS_KNOWLEDGE_ENABLED", "false")
    model = ToolCallingFakeModel(responses=[AIMessage(content="hello")])

    agent = create_lead_agent(model)

    assert "knowledge_search" not in agent.nodes["tools"].bound.tools_by_name
    assert "knowledge_ingest" not in agent.nodes["tools"].bound.tools_by_name
    assert "knowledge_list" not in agent.nodes["tools"].bound.tools_by_name
    assert "knowledge_delete" not in agent.nodes["tools"].bound.tools_by_name


def test_lead_agent_persists_hidden_dynamic_date(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ZHARNESS_SKILLS_PATH", str(tmp_path / "no-skills"))
    monkeypatch.setenv("ZHARNESS_TIMEZONE", "Asia/Shanghai")
    model = ToolCallingFakeModel(responses=[AIMessage(content="hello")])
    agent = create_lead_agent(model)

    result = agent.invoke({"messages": [{"role": "user", "content": "hello"}]})

    reminders = [
        message
        for message in result["messages"]
        if isinstance(message, SystemMessage)
        and message.additional_kwargs.get(DYNAMIC_DATE_MARKER)
    ]
    assert len(reminders) == 1
    assert reminders[0].additional_kwargs["reminder_timezone"] == "Asia/Shanghai"


def test_create_lead_agent_registers_describe_skill_when_skills_exist(
    tmp_path, monkeypatch
) -> None:
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "public" / "deep-research"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: deep-research\n"
        "description: Do web research.\n"
        "---\n\n# Deep Research\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ZHARNESS_SKILLS_PATH", str(skills_dir))
    model = FakeMessagesListChatModel(responses=[AIMessage(content="hello")])

    agent = create_lead_agent(model)

    assert "describe_skill" in agent.nodes["tools"].bound.tools_by_name


def test_create_lead_agent_omits_describe_skill_when_all_disabled(
    tmp_path, monkeypatch
) -> None:
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "public" / "deep-research"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: deep-research\n"
        "description: Do web research.\n"
        "---\n\n# Deep Research\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    state = home / "skills_state.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        '{"version": 1, "skills": {"deep-research": false}}', encoding="utf-8"
    )
    monkeypatch.setenv("ZHARNESS_SKILLS_PATH", str(skills_dir))
    monkeypatch.setenv("ZHARNESS_HOME", str(home))
    model = FakeMessagesListChatModel(responses=[AIMessage(content="hello")])

    agent = create_lead_agent(model)

    assert "describe_skill" not in agent.nodes["tools"].bound.tools_by_name


def _flaky_sandbox(attempts_before_success: int) -> tuple[SimpleNamespace, list[int]]:
    """Build a sandbox that raises on the first calls and then succeeds. / 构建一个前几次调用抛错、随后成功的沙箱。"""
    attempts: list[int] = []

    def read(path, offset, limit):
        attempts.append(1)
        if len(attempts) < attempts_before_success:
            raise RuntimeError("transient failure")
        return ReadResult(file_data={"content": "data", "encoding": "utf-8"})

    sandbox = SimpleNamespace(read=read)
    return sandbox, attempts


def test_lead_agent_retries_transient_tool_errors(monkeypatch) -> None:
    thread_ids: list[str] = []
    sandbox, attempts = _flaky_sandbox(attempts_before_success=2)
    manager = SimpleNamespace(
        for_thread=lambda thread_id: thread_ids.append(thread_id) or sandbox
    )
    monkeypatch.setattr(workspace_module, "get_sandbox_manager", lambda: manager)
    model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"path": "/workspace/notes/result.txt"},
                        "id": "call-read",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Read complete."),
        ]
    )
    agent = create_lead_agent(model)

    result = agent.invoke(
        {"messages": [{"role": "user", "content": "Read the file."}]},
        {"configurable": {"thread_id": "retry-thread"}},
    )

    assert attempts == [1, 1]
    tool_messages = [
        message for message in result["messages"] if isinstance(message, ToolMessage)
    ]
    assert tool_messages[-1].content == "data"
    assert tool_messages[-1].status == "success"


def test_lead_agent_surfaces_persistent_tool_errors(monkeypatch) -> None:
    thread_ids: list[str] = []
    sandbox, attempts = _flaky_sandbox(attempts_before_success=99)
    manager = SimpleNamespace(
        for_thread=lambda thread_id: thread_ids.append(thread_id) or sandbox
    )
    monkeypatch.setattr(workspace_module, "get_sandbox_manager", lambda: manager)
    model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"path": "/workspace/notes/result.txt"},
                        "id": "call-read",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Read failed."),
        ]
    )
    agent = create_lead_agent(model)

    result = agent.invoke(
        {"messages": [{"role": "user", "content": "Read the file."}]},
        {"configurable": {"thread_id": "error-thread"}},
    )

    assert len(attempts) == 4
    tool_messages = [
        message for message in result["messages"] if isinstance(message, ToolMessage)
    ]
    assert tool_messages[-1].status == "error"
    assert "failed unexpectedly" in str(tool_messages[-1].content)
    assert "transient failure" not in str(tool_messages[-1].content)


def test_lead_agent_does_not_retry_mutating_tool_errors(monkeypatch) -> None:
    """Do not repeat a write whose outcome may already have side effects. / 不重复可能已经产生副作用的写入。"""
    attempts: list[int] = []

    def fail_write(path, content):
        attempts.append(1)
        raise RuntimeError("uncertain failure after a write attempt")

    manager = SimpleNamespace(
        for_thread=lambda thread_id: SimpleNamespace(write=fail_write)
    )
    monkeypatch.setattr(workspace_module, "get_sandbox_manager", lambda: manager)
    model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "path": "/workspace/result.txt",
                            "content": "data",
                        },
                        "id": "call-write",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Write failed."),
        ]
    )
    agent = create_lead_agent(model)

    result = agent.invoke(
        {"messages": [{"role": "user", "content": "Write the file."}]},
        {"configurable": {"thread_id": "write-error-thread"}},
    )

    assert attempts == [1]
    tool_messages = [
        message for message in result["messages"] if isinstance(message, ToolMessage)
    ]
    assert tool_messages[-1].status == "error"

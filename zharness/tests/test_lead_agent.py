from types import SimpleNamespace

from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage, ToolMessage
from zharness.agents.lead import create_lead_agent
from zharness.sandbox.protocol import ReadResult
from zharness.tools import workspace as workspace_module


class ToolCallingFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        _ = tools, tool_choice, kwargs
        return self


def test_create_lead_agent(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ZHARNESS_SKILLS_PATH", str(tmp_path / "no-skills"))
    model = FakeMessagesListChatModel(responses=[AIMessage(content="hello")])

    agent = create_lead_agent(model)

    assert agent.name == "lead_agent"
    assert agent.context_schema is None
    assert "SummarizationMiddleware.before_model" in agent.nodes
    assert "HumanInTheLoopMiddleware.after_model" in agent.nodes
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
        "task",
    }


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
                        "args": {"path": "/notes/result.txt"},
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
                        "args": {"path": "/notes/result.txt"},
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
    assert "RuntimeError" in str(tool_messages[-1].content)

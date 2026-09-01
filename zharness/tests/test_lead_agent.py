from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage
from zharness.agents.lead import create_lead_agent


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

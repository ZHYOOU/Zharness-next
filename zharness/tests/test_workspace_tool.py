from pathlib import Path
from types import SimpleNamespace
from typing import cast

from langchain.tools import ToolRuntime
from zharness.agents.context import AgentContext
from zharness.tools.workspace import list_workspace


def test_list_workspace_hides_runtime_from_model_schema() -> None:
    assert list_workspace.args == {}


def test_list_workspace_reads_path_from_runtime_context(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hello", encoding="utf-8")
    runtime = cast(
        ToolRuntime[AgentContext],
        SimpleNamespace(context=AgentContext(workspace_path=str(tmp_path))),
    )

    assert list_workspace.func(runtime) == ["hello.txt"]

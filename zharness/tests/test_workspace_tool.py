from pathlib import Path
from types import SimpleNamespace
from typing import cast

from langchain.tools import ToolRuntime
from zharness.agents.context import AgentContext
from zharness.tools.workspace import list_workspace, read_file


def test_read_file_only_exposes_path_to_model() -> None:
    assert set(read_file.args) == {"path"}


def test_list_workspace_hides_runtime_from_model_schema() -> None:
    assert list_workspace.args == {}


def test_list_workspace_reads_path_from_runtime_context(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hello", encoding="utf-8")
    runtime = cast(
        ToolRuntime[AgentContext],
        SimpleNamespace(context=AgentContext(workspace_path=str(tmp_path))),
    )

    assert list_workspace.func(runtime) == ["hello.txt"]


def test_read_file_uses_runtime_workspace(
    tmp_path: Path,
) -> None:
    (tmp_path / "hello.txt").write_text(
        "hello",
        encoding="utf-8",
    )

    runtime = cast(
        ToolRuntime[AgentContext],
        SimpleNamespace(
            context=AgentContext(
                workspace_path=str(tmp_path),
            )
        ),
    )

    assert (
        read_file.func(
            "hello.txt",
            runtime,
        )
        == "hello"
    )


def test_read_file_returns_recoverable_error(tmp_path: Path) -> None:
    runtime = cast(
        ToolRuntime[AgentContext],
        SimpleNamespace(context=AgentContext(workspace_path=str(tmp_path))),
    )

    assert read_file.func("../missing.txt", runtime) == (
        "Error: Path escapes the workspace"
    )

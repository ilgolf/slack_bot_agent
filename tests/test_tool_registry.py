"""ToolRegistry behavior for the bounded code-agent tool loop.

`ProjectExecutionTools.tool_registry()` is the only place these tools are wired
up for iterative tool-calling, so these tests exercise it end to end via
`ToolRegistry.invoke` rather than calling the underlying methods directly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from src.execution_workflow import ProjectExecutionTools
from src.tool_policy import ToolCategory
from src.tool_registry import ToolArgument, ToolRegistry, definition


def test_list_files_invoke_with_no_args_lists_project_root(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "a.txt").write_text("hello")
    (project / "sub").mkdir()
    registry = ProjectExecutionTools(project, []).tool_registry()

    outcome = registry.invoke("list_files")

    assert outcome.status == "ok"
    assert "a.txt" in outcome.safe_message
    assert "sub" in outcome.safe_message


def test_grep_invoke_with_no_path_searches_project_root(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "a.txt").write_text("needle here")
    (project / "sub").mkdir()
    (project / "sub" / "b.txt").write_text("needle there")
    registry = ProjectExecutionTools(project, []).tool_registry()

    outcome = registry.invoke("grep", {"query": "needle"})

    assert outcome.status == "ok"
    assert "a.txt" in outcome.safe_message
    assert "sub/b.txt" in outcome.safe_message


def test_read_file_invoke_still_rejects_project_root(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    registry = ProjectExecutionTools(project, []).tool_registry()

    outcome = registry.invoke("read_file", {"relative_path": "."})

    assert outcome.status == "failed"


def test_invoke_with_unknown_argument_name_does_not_raise(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    registry = ProjectExecutionTools(project, []).tool_registry()

    outcome = registry.invoke("read_file", {"path": "x"})

    assert outcome.status == "failed"
    assert outcome.retryable is True


def test_grep_skips_git_venv_node_modules_and_pycache_dirs(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "a.txt").write_text("needle")
    for skipped_dir in (".git", ".venv", "node_modules", "__pycache__"):
        nested = project / skipped_dir / "nested"
        nested.mkdir(parents=True)
        (nested / "b.txt").write_text("needle")
    registry = ProjectExecutionTools(project, []).tool_registry()

    outcome = registry.invoke("grep", {"query": "needle"})

    assert outcome.status == "ok"
    assert outcome.safe_message == "['a.txt']"


def test_read_file_invoke_truncates_oversized_file(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "big.txt").write_text("x" * 40_000)
    registry = ProjectExecutionTools(project, []).tool_registry()

    outcome = registry.invoke("read_file", {"relative_path": "big.txt"})

    assert outcome.status == "truncated"
    assert len(outcome.safe_message.encode("utf-8")) <= 32_000


def test_definition_derives_argument_schema_from_handler_signature() -> None:
    def handler(relative_path: str, limit: int = 10) -> str:
        return relative_path + str(limit)

    tool_def = definition("read_slice", ToolCategory.PROJECT_READ, handler)

    assert tool_def.arguments == (
        ToolArgument(name="relative_path", type=str, required=True),
        ToolArgument(name="limit", type=int, required=False),
    )


def test_read_file_invoke_populates_evidence_on_success(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "app.py").write_text("print('hi')")
    registry = ProjectExecutionTools(project, []).tool_registry()

    outcome = registry.invoke("read_file", {"relative_path": "app.py"})

    assert outcome.status == "ok"
    assert outcome.evidence == ("app.py",)


def test_as_langchain_tools_matches_registry_names_and_arguments() -> None:
    def read_file(relative_path: str) -> str:
        return relative_path

    def list_files(relative_path: str = ".") -> list[str]:
        del relative_path
        return []

    registry = ToolRegistry(
        [
            definition("read_file", ToolCategory.PROJECT_READ, read_file),
            definition("list_files", ToolCategory.PROJECT_READ, list_files),
        ]
    )

    tools = {tool.name: tool for tool in registry.as_langchain_tools()}

    assert set(tools) == {"read_file", "list_files"}
    assert set(tools["read_file"].args) == {"relative_path"}
    assert set(tools["list_files"].args) == {"relative_path"}


def test_as_langchain_tools_excludes_blocked_category() -> None:
    def read_file(relative_path: str) -> str:
        return relative_path

    def delete_file(relative_path: str) -> None:
        del relative_path

    registry = ToolRegistry(
        [
            definition("read_file", ToolCategory.PROJECT_READ, read_file),
            definition("delete_file", ToolCategory.BLOCKED, delete_file),
        ]
    )

    tools = {tool.name for tool in registry.as_langchain_tools()}

    assert tools == {"read_file"}


def test_invoke_with_missing_required_argument_does_not_call_handler() -> None:
    calls: list[dict[str, object]] = []

    def handler(relative_path: str) -> str:
        calls.append({"relative_path": relative_path})
        return "content"

    registry = ToolRegistry([definition("read_file", ToolCategory.PROJECT_READ, handler)])

    outcome = registry.invoke("read_file", {})

    assert outcome.status == "failed"
    assert calls == []


def test_invoke_normalizes_subprocess_timeout() -> None:
    def slow() -> str:
        raise subprocess.TimeoutExpired(cmd="uv run pytest", timeout=120)

    registry = ToolRegistry([definition("run_tests", ToolCategory.VERIFY, slow)])

    outcome = registry.invoke("run_tests", confirmed=True)

    assert outcome.status == "timeout"

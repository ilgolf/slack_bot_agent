"""ToolRegistry behavior for the bounded code-agent tool loop.

Each test registers its own handlers and drives them through `ToolRegistry.invoke`.
"""

from __future__ import annotations

import subprocess

from src.tool_policy import ToolCategory
from src.tool_registry import ToolArgument, ToolRegistry, definition


def test_definition_derives_argument_schema_from_handler_signature() -> None:
    def handler(relative_path: str, limit: int = 10) -> str:
        return relative_path + str(limit)

    tool_def = definition("read_slice", ToolCategory.PROJECT_READ, handler)

    assert tool_def.arguments == (
        ToolArgument(name="relative_path", type=str, required=True),
        ToolArgument(name="limit", type=int, required=False),
    )


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

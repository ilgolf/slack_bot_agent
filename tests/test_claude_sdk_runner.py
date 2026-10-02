"""ClaudeSdkRunner: runs the Claude Agent SDK read-only for project analysis
(plan.md Phase 12, section E). No test starts a real SDK or CLI.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    Message,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from src.claude_sdk_runner import ClaudeSdkRunner, files_read_from, read_only_options
from src.code_agent_analysis import RunnerError, RunnerTimeout

_MUTATING_TOOLS = {"Edit", "Write", "Bash", "NotebookEdit"}


def test_options_limit_available_tools_to_read_only_tools() -> None:
    options = read_only_options(cwd=Path("/work/project"), max_turns=7)

    assert options.tools == ["Read", "Grep", "Glob"]
    assert not _MUTATING_TOOLS & set(options.tools or [])
    assert not _MUTATING_TOOLS & set(options.allowed_tools)


def test_options_do_not_load_user_or_project_settings() -> None:
    options = read_only_options(cwd=Path("/work/project"), max_turns=7)

    assert options.setting_sources == []


def test_options_deny_tools_outside_the_allow_list_without_prompting() -> None:
    options = read_only_options(cwd=Path("/work/project"), max_turns=7)

    assert options.permission_mode == "dontAsk"


def test_options_set_cwd_and_max_turns_from_arguments() -> None:
    options = read_only_options(cwd=Path("/work/project"), max_turns=7)

    assert options.cwd == Path("/work/project")
    assert options.max_turns == 7


def test_files_read_come_from_read_tool_use_blocks_of_assistant_messages() -> None:
    messages = [
        AssistantMessage(
            content=[
                TextBlock(text="구조를 확인합니다"),
                ToolUseBlock(id="1", name="Glob", input={"pattern": "**/*.java"}),
                ToolUseBlock(id="2", name="Read", input={"file_path": "/work/p/core/Order.java"}),
            ],
            model="m",
        ),
        AssistantMessage(
            content=[
                ToolUseBlock(id="3", name="Read", input={"file_path": "/work/p/core/Pay.java"})
            ],
            model="m",
        ),
    ]

    assert files_read_from(messages) == (
        Path("/work/p/core/Order.java"),
        Path("/work/p/core/Pay.java"),
    )


def _result_message(result: str) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="s",
        result=result,
    )


def test_run_returns_the_final_result_text_and_files_read(tmp_path: Path) -> None:
    prompts: list[str] = []

    async def fake_query(prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        prompts.append(prompt)
        yield AssistantMessage(
            content=[ToolUseBlock(id="1", name="Read", input={"file_path": "/work/p/Order.java"})],
            model="m",
        )
        yield _result_message('{"summary": "done", "findings": []}')

    runner = ClaudeSdkRunner(query=fake_query)

    result = runner.run("분석해", cwd=tmp_path, timeout_seconds=30.0, max_turns=5)

    assert prompts == ["분석해"]
    assert result.text == '{"summary": "done", "findings": []}'
    assert result.files_read == (Path("/work/p/Order.java"),)


def test_run_raises_runner_timeout_when_the_stream_exceeds_the_time_budget(
    tmp_path: Path,
) -> None:
    async def slow_query(prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        await asyncio.sleep(5)
        yield _result_message("too late")

    runner = ClaudeSdkRunner(query=slow_query)

    with pytest.raises(RunnerTimeout):
        runner.run("분석해", cwd=tmp_path, timeout_seconds=0.05, max_turns=5)


def test_run_converts_sdk_errors_into_runner_error_without_the_original_message(
    tmp_path: Path,
) -> None:
    async def failing_query(prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        raise ClaudeSDKError("auth failed: sk-secret-token")
        yield  # pragma: no cover - makes this an async generator

    runner = ClaudeSdkRunner(query=failing_query)

    with pytest.raises(RunnerError) as exc_info:
        runner.run("분석해", cwd=tmp_path, timeout_seconds=30.0, max_turns=5)

    assert "sk-secret-token" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__


def test_run_raises_runner_error_when_the_result_message_reports_an_error(
    tmp_path: Path,
) -> None:
    async def error_query(prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        yield ResultMessage(
            subtype="error_max_turns",
            duration_ms=1,
            duration_api_ms=1,
            is_error=True,
            num_turns=5,
            session_id="s",
        )

    runner = ClaudeSdkRunner(query=error_query)

    with pytest.raises(RunnerError):
        runner.run("분석해", cwd=tmp_path, timeout_seconds=30.0, max_turns=5)


def test_complete_runs_without_tools_in_an_empty_isolated_directory() -> None:
    captured: list[ClaudeAgentOptions] = []
    listing: list[list[str]] = []

    async def fake_query(prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        captured.append(options)
        listing.append([p.name for p in Path(str(options.cwd)).iterdir()])
        yield _result_message("요약")

    text = ClaudeSdkRunner(query=fake_query).complete("요약해", timeout_seconds=30.0)

    options = captured[0]
    assert text == "요약"
    assert options.tools == []
    assert options.allowed_tools == []
    assert options.max_turns == 1
    assert options.setting_sources == []
    assert options.permission_mode == "dontAsk"
    assert listing == [[]]

"""CodexSdkRunner: runs the Codex SDK read-only for project analysis (plan.md
Phase 12, section F). No test starts a real SDK or CLI.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from openai_codex_sdk import (
    AgentMessageItem,
    CommandExecutionItem,
    CommandExecutionStatus,
    ItemCompletedEvent,
    StreamedTurn,
    ThreadError,
    ThreadErrorEvent,
    ThreadEvent,
    ThreadOptions,
    TurnFailedEvent,
)
from openai_codex_sdk.errors import CodexSdkError

from src.code.analysis import RunnerError, RunnerTimeout
from src.code.codex_sdk_runner import CodexSdkRunner, files_read_from, read_only_thread_options


def test_thread_options_are_read_only_without_approvals_and_scoped_to_the_project() -> None:
    options = read_only_thread_options(cwd=Path("/work/project"))

    assert options.sandbox_mode == "read-only"
    assert options.approval_policy == "never"
    assert options.working_directory == "/work/project"


def test_thread_options_disable_network_and_web_search() -> None:
    options = read_only_thread_options(cwd=Path("/work/project"))

    assert options.network_access_enabled is False
    assert options.web_search_enabled is False


def _command_event(
    command: str, *, exit_code: int = 0, status: CommandExecutionStatus = "completed"
) -> ItemCompletedEvent:
    return ItemCompletedEvent(
        type="item.completed",
        item=CommandExecutionItem(
            id="1",
            type="command_execution",
            command=command,
            aggregated_output="",
            exit_code=exit_code,
            status=status,
        ),
    )


@pytest.mark.parametrize(
    "command",
    [
        "cat core/Order.java",
        "/bin/zsh -lc \"sed -n '1,200p' core/Order.java\"",
        "head -n 20 core/Order.java",
        "tail -n 20 core/Order.java",
        "nl -ba core/Order.java",
        "bash -lc 'cd . && cat core/Order.java'",
    ],
)
def test_files_read_come_from_known_read_commands(command: str) -> None:
    cwd = Path("/work/project")

    assert files_read_from([_command_event(command)], cwd=cwd) == (
        Path("/work/project/core/Order.java"),
    )


@pytest.mark.parametrize(
    "command",
    [
        "rg -n Order core",
        "ls -la core",
        "find . -name '*.java'",
        '/bin/zsh -lc "rg --files core | head -50"',
    ],
)
def test_search_and_listing_commands_do_not_count_as_files_read(command: str) -> None:
    assert files_read_from([_command_event(command)], cwd=Path("/work/project")) == ()


@pytest.mark.parametrize(
    "event",
    [
        _command_event("cat core/Missing.java", exit_code=1),
        _command_event("cat core/Order.java", status="failed"),
    ],
)
def test_failed_commands_do_not_count_as_files_read(event: ItemCompletedEvent) -> None:
    assert files_read_from([event], cwd=Path("/work/project")) == ()


@pytest.mark.parametrize(
    "command",
    [
        "cat core/Order.java 2>/dev/null",
        "cat core/Order.java 2>&1",
        "cat core/Order.java > out.txt",
        "cat < core/Order.java",
    ],
)
def test_redirections_are_not_counted_as_file_arguments(command: str) -> None:
    expected = (Path("/work/project/core/Order.java"),)

    assert files_read_from([_command_event(command)], cwd=Path("/work/project")) == expected


def _agent_message_event(text: str) -> ItemCompletedEvent:
    return ItemCompletedEvent(
        type="item.completed",
        item=AgentMessageItem(id="m", type="agent_message", text=text),
    )


@dataclass
class FakeThread:
    events: list[ThreadEvent]
    prompts: list[str] = field(default_factory=list)

    async def run_streamed(self, prompt: str) -> StreamedTurn:
        self.prompts.append(prompt)

        async def stream() -> AsyncIterator[ThreadEvent]:
            for event in self.events:
                yield event

        return StreamedTurn(events=stream())


def test_run_returns_the_last_agent_message_and_files_read(tmp_path: Path) -> None:
    thread = FakeThread(
        events=[
            _command_event("cat core/Order.java"),
            _agent_message_event("중간 메모"),
            _agent_message_event('{"summary": "done", "findings": []}'),
        ]
    )
    started: list[ThreadOptions] = []

    def start_thread(options: ThreadOptions) -> FakeThread:
        started.append(options)
        return thread

    runner = CodexSdkRunner(start_thread=start_thread)

    result = runner.run("분석해", cwd=tmp_path, timeout_seconds=30.0, max_turns=5)

    assert thread.prompts == ["분석해"]
    assert started[0].working_directory == str(tmp_path)
    assert result.text == '{"summary": "done", "findings": []}'
    assert result.files_read == (tmp_path / "core" / "Order.java",)


def test_run_raises_runner_timeout_when_the_stream_exceeds_the_time_budget(
    tmp_path: Path,
) -> None:
    class SlowThread:
        async def run_streamed(self, prompt: str) -> StreamedTurn:
            async def stream() -> AsyncIterator[ThreadEvent]:
                await asyncio.sleep(5)
                yield _agent_message_event("too late")

            return StreamedTurn(events=stream())

    runner = CodexSdkRunner(start_thread=lambda options: SlowThread())

    with pytest.raises(RunnerTimeout):
        runner.run("분석해", cwd=tmp_path, timeout_seconds=0.05, max_turns=5)


@pytest.mark.parametrize(
    "events",
    [
        [TurnFailedEvent(type="turn.failed", error=ThreadError(message="token sk-secret"))],
        [ThreadErrorEvent(type="error", message="token sk-secret")],
    ],
)
def test_run_converts_failure_events_into_runner_error_without_the_original_message(
    tmp_path: Path, events: list[ThreadEvent]
) -> None:
    runner = CodexSdkRunner(start_thread=lambda options: FakeThread(events=events))

    with pytest.raises(RunnerError) as exc_info:
        runner.run("분석해", cwd=tmp_path, timeout_seconds=30.0, max_turns=5)

    assert "sk-secret" not in str(exc_info.value)


def test_run_converts_sdk_exceptions_into_runner_error_without_the_original_message(
    tmp_path: Path,
) -> None:
    class FailingThread:
        async def run_streamed(self, prompt: str) -> StreamedTurn:
            raise CodexSdkError("token sk-secret")

    runner = CodexSdkRunner(start_thread=lambda options: FailingThread())

    with pytest.raises(RunnerError) as exc_info:
        runner.run("분석해", cwd=tmp_path, timeout_seconds=30.0, max_turns=5)

    assert "sk-secret" not in str(exc_info.value)
    assert exc_info.value.__suppress_context__


def test_complete_runs_read_only_without_network_in_an_empty_directory() -> None:
    thread = FakeThread(events=[_agent_message_event("요약")])
    started: list[ThreadOptions] = []
    listing: list[list[str]] = []

    def start_thread(options: ThreadOptions) -> FakeThread:
        started.append(options)
        listing.append([p.name for p in Path(str(options.working_directory)).iterdir()])
        return thread

    text = CodexSdkRunner(start_thread=start_thread).complete("요약해", timeout_seconds=30.0)

    assert text == "요약"
    assert started[0].sandbox_mode == "read-only"
    assert started[0].network_access_enabled is False
    assert listing == [[]]

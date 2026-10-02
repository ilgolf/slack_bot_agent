"""Claude Agent SDK runner for read-only project analysis (plan.md Phase 12).

Needs the optional `claude` extra (`claude-agent-sdk`).
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    Message,
    ResultMessage,
    ToolUseBlock,
    query,
)

from src.code_agent_analysis import RunnerError, RunnerResult, RunnerTimeout
from src.read_path_guard import is_allowed_read

_READ_ONLY_TOOLS = ["Read", "Grep", "Glob"]

QueryFn = Callable[[str, ClaudeAgentOptions], AsyncIterator[Message]]


def _sdk_query(prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
    return query(prompt=prompt, options=options)


class ClaudeSdkRunner:
    """Sync `AgentRunner` over the async SDK stream. Slack handlers run in worker
    threads, so `asyncio.run` here never nests inside a running event loop."""

    name = "claude_code"

    def __init__(self, *, query: QueryFn = _sdk_query) -> None:
        self._query = query

    def run(
        self, prompt: str, *, cwd: Path, timeout_seconds: float, max_turns: int
    ) -> RunnerResult:
        return self._execute(
            prompt, read_only_options(cwd=cwd, max_turns=max_turns), timeout_seconds
        )

    def complete(self, prompt: str, *, timeout_seconds: float) -> str:
        """Answer from the prompt alone: no tools, one turn, an empty scratch directory."""
        with tempfile.TemporaryDirectory() as scratch:
            options = text_only_options(cwd=Path(scratch))
            return self._execute(prompt, options, timeout_seconds).text

    def _execute(
        self, prompt: str, options: ClaudeAgentOptions, timeout_seconds: float
    ) -> RunnerResult:
        try:
            return asyncio.run(
                asyncio.wait_for(self._collect(prompt, options), timeout=timeout_seconds)
            )
        except TimeoutError:
            raise RunnerTimeout from None
        except ClaudeSDKError:
            raise RunnerError("Claude Agent SDK 실행에 실패했습니다.") from None

    async def _collect(self, prompt: str, options: ClaudeAgentOptions) -> RunnerResult:
        messages = [message async for message in self._query(prompt, options)]
        final = next((m for m in reversed(messages) if isinstance(m, ResultMessage)), None)
        if final is not None and final.is_error:
            raise RunnerError(f"Claude Agent SDK가 오류 결과를 반환했습니다: {final.subtype}")
        return RunnerResult(
            text=(final.result or "") if final else "", files_read=files_read_from(messages)
        )


def _pre_tool_use_guard(
    is_allowed: Callable[[str, Mapping[str, object]], bool], reason: str, tools: Sequence[str]
) -> HookMatcher:
    """One `PreToolUse` hook that denies every tool call `is_allowed` rejects."""

    async def guard(
        hook_input: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        if hook_input["hook_event_name"] != "PreToolUse":
            return {}
        if is_allowed(hook_input["tool_name"], hook_input["tool_input"]):
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    return HookMatcher(matcher="|".join(tools), hooks=[guard])


def read_only_options(*, cwd: Path, max_turns: int) -> ClaudeAgentOptions:
    """`tools` removes every other built-in tool from the model's context; the same
    list in `allowed_tools` only pre-approves these so no prompt blocks a headless run."""
    project_root = Path(cwd)
    return ClaudeAgentOptions(
        tools=list(_READ_ONLY_TOOLS),
        allowed_tools=list(_READ_ONLY_TOOLS),
        cwd=cwd,
        max_turns=max_turns,
        # `[]` is SDK isolation mode: no `~/.claude` settings, hooks or CLAUDE.md leak in.
        setting_sources=[],
        permission_mode="dontAsk",
        hooks={
            "PreToolUse": [
                _pre_tool_use_guard(
                    lambda tool, tool_input: is_allowed_read(project_root, tool, tool_input),
                    "프로젝트 밖 경로는 읽을 수 없습니다.",
                    _READ_ONLY_TOOLS,
                )
            ]
        },
    )


def text_only_options(*, cwd: Path) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        tools=[],
        allowed_tools=[],
        cwd=cwd,
        max_turns=1,
        setting_sources=[],
        permission_mode="dontAsk",
    )


def files_read_from(messages: Iterable[Message]) -> tuple[Path, ...]:
    """Only `Read` proves a file's content was seen: `Grep`/`Glob` take a search
    directory or pattern, not a file that was read, so they add no evidence."""
    return tuple(
        Path(block.input["file_path"])
        for message in messages
        if isinstance(message, AssistantMessage)
        for block in message.content
        if isinstance(block, ToolUseBlock)
        and block.name == "Read"
        and isinstance(block.input.get("file_path"), str)
    )

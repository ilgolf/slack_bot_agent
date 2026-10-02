"""Codex SDK runner for read-only project analysis (plan.md Phase 12).

Needs the optional `codex` extra (`openai-codex-sdk`) and a `codex` executable.
"""

from __future__ import annotations

import asyncio
import shlex
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Protocol

from openai_codex_sdk import (
    AgentMessageItem,
    Codex,
    CommandExecutionItem,
    ItemCompletedEvent,
    StreamedTurn,
    Thread,
    ThreadErrorEvent,
    ThreadEvent,
    ThreadOptions,
    TurnFailedEvent,
)
from openai_codex_sdk.errors import CodexSdkError

from src.code_agent_analysis import RunnerError, RunnerResult, RunnerTimeout

_SHELLS = frozenset({"sh", "bash", "zsh"})
_REDIRECTIONS = frozenset({"<", ">", ">>", ">&", "&>", "&>>"})
_VALUE_OPTIONS = {
    "head": frozenset({"-n", "-c"}),
    "tail": frozenset({"-n", "-c"}),
    "nl": frozenset({"-b", "-d", "-f", "-h", "-i", "-l", "-n", "-s", "-v", "-w"}),
}


class ThreadLike(Protocol):
    async def run_streamed(self, prompt: str) -> StreamedTurn: ...


StartThread = Callable[[ThreadOptions], ThreadLike]


def codex_thread_starter(codex_path: str) -> StartThread:
    def start_thread(options: ThreadOptions) -> Thread:
        return Codex({"codex_path_override": codex_path}).start_thread(options)

    return start_thread


class CodexSdkRunner:
    """Sync `AgentRunner` over the async Codex event stream. Codex has no turn limit
    option, so `max_turns` is not applied here."""

    name = "codex"

    def __init__(self, *, start_thread: StartThread) -> None:
        self._start_thread = start_thread

    def run(
        self, prompt: str, *, cwd: Path, timeout_seconds: float, max_turns: int
    ) -> RunnerResult:
        del max_turns
        thread = self._start_thread(read_only_thread_options(cwd=cwd))
        try:
            events = asyncio.run(
                asyncio.wait_for(self._collect(thread, prompt), timeout=timeout_seconds)
            )
        except TimeoutError:
            raise RunnerTimeout from None
        except CodexSdkError:
            raise RunnerError("Codex SDK 실행에 실패했습니다.") from None
        if any(isinstance(event, TurnFailedEvent | ThreadErrorEvent) for event in events):
            raise RunnerError("Codex가 실패 이벤트를 반환했습니다.")
        return RunnerResult(
            text=_last_agent_message(events), files_read=files_read_from(events, cwd=cwd)
        )

    def complete(self, prompt: str, *, timeout_seconds: float) -> str:
        """Answer from the prompt alone. Codex cannot switch tools off, so it runs
        read-only, offline, in an empty scratch directory with nothing to read."""
        with tempfile.TemporaryDirectory() as scratch:
            return self.run(
                prompt, cwd=Path(scratch), timeout_seconds=timeout_seconds, max_turns=1
            ).text

    @staticmethod
    async def _collect(thread: ThreadLike, prompt: str) -> list[ThreadEvent]:
        turn = await thread.run_streamed(prompt)
        return [event async for event in turn.events]


def _last_agent_message(events: list[ThreadEvent]) -> str:
    messages = [
        event.item.text
        for event in events
        if isinstance(event, ItemCompletedEvent) and isinstance(event.item, AgentMessageItem)
    ]
    return messages[-1] if messages else ""


def read_only_thread_options(*, cwd: Path) -> ThreadOptions:
    # camelCase aliases: the pydantic model's field names are not visible to mypy's
    # pydantic plugin, while the aliases are.
    return ThreadOptions(
        sandboxMode="read-only",
        approvalPolicy="never",
        workingDirectory=str(cwd),
        networkAccessEnabled=False,
        webSearchEnabled=False,
    )


def files_read_from(events: Iterable[ThreadEvent], *, cwd: Path) -> tuple[Path, ...]:
    """Codex reads files through shell commands, so evidence is inferred from the
    file arguments of known read commands; search commands (`rg`, `ls`, `find`)
    add nothing. Heuristic by nature — weaker than Claude's `Read` tool blocks."""
    return tuple(
        cwd / argument
        for event in events
        if isinstance(event, ItemCompletedEvent)
        and isinstance(event.item, CommandExecutionItem)
        and _succeeded(event.item)
        for argument in _files_in_command(event.item.command)
    )


def _succeeded(item: CommandExecutionItem) -> bool:
    """A failed `cat` of a missing file must not pass as evidence."""
    return item.status == "completed" and item.exit_code in (None, 0)


def _files_in_command(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []
    files: list[str] = []
    for argv in _segments(tokens):
        script = _shell_script(argv)
        files.extend(_files_in_command(script) if script is not None else _read_files(argv))
    return files


def _segments(tokens: list[str]) -> list[list[str]]:
    """Split on control operators. Output redirections drop their target (and a
    leading fd number like the `2` of `2>&1`); `< file` is an input, i.e. a file read."""
    segments: list[list[str]] = [[]]
    pending_redirect: str | None = None
    for token in tokens:
        if pending_redirect is not None:
            if pending_redirect == "<":
                segments[-1].append(token)
            pending_redirect = None
        elif token in _REDIRECTIONS:
            if segments[-1] and segments[-1][-1].isdigit():
                segments[-1].pop()
            pending_redirect = token
        elif all(character in "&|;<>()" for character in token):
            segments.append([])
        else:
            segments[-1].append(token)
    return [segment for segment in segments if segment]


def _shell_script(argv: list[str]) -> str | None:
    if Path(argv[0]).name not in _SHELLS:
        return None
    for index, token in enumerate(argv[1:-1], start=1):
        if token.startswith("-") and token.endswith("c"):
            return argv[index + 1]
    return None


def _read_files(argv: list[str]) -> list[str]:
    name, args = Path(argv[0]).name, argv[1:]
    if name == "cat":
        return _positional(args, frozenset())
    if name in {"head", "tail", "nl"}:
        return _positional(args, _VALUE_OPTIONS[name])
    if name == "sed" and "-e" not in args:
        return _positional(args, frozenset())[1:]  # first positional is the script
    return []


def _positional(args: list[str], value_options: frozenset[str]) -> list[str]:
    positional: list[str] = []
    skip_value = False
    for argument in args:
        if skip_value:
            skip_value = False
        elif argument in value_options:
            skip_value = True
        elif not argument.startswith("-"):
            positional.append(argument)
    return positional

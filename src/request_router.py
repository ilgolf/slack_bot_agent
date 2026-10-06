"""Deterministic, typed routing before any LLM or external tool call."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class RequestIntent(StrEnum):
    CONTROL_CONFIRM = "control_confirm"
    CONTROL_CANCEL = "control_cancel"
    PLAN_CONFIRM = "plan_confirm"
    CODE_WORK = "code_work"
    LINEAR_READ = "linear_read"
    LINEAR_MUTATION = "linear_mutation"
    PROJECT_ANALYSIS = "project_analysis"
    THREAD_SUMMARY = "thread_summary"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class RoutedRequest:
    intent: RequestIntent
    text: str
    project_name: str | None = None
    confirmation_verb: str | None = None
    mode_decided: bool = False
    area: str | None = None


_MENTION = re.compile(r"<@[^>]+>")
_CONFIRM = re.compile(r"^(실행|실행해줘|실행합니다)$")
_CANCEL = re.compile(r"^(취소|취소해줘|취소합니다)$")
_DISCARD = re.compile(r"^(폐기|폐기해줘|폐기합니다)$")
_PLAN_CONFIRM = re.compile(
    r"^기획\s*확정\s+(linear|code|notion)(?:\s*(?:해줘|합니다))?$", re.IGNORECASE
)
# Asking about the feature is a question, not a request to run it.
_FEATURE_QUESTION_MARKERS = ("기능", "가능", "지원", "어떻게", "방법")
_LINEAR_CREATE = ("이슈 생성", "티켓 생성", "이슈 추가", "티켓 추가", "이슈 만들", "티켓 만들")
_LINEAR_UPDATE = ("이슈 수정", "티켓 수정", "이슈 변경", "티켓 변경", "이슈 업데이트")
_LINEAR_READ = (
    "연결 상태",
    "팀 조회",
    "팀 목록",
    "팀 리스트",
    "이슈 조회",
    "이슈 목록",
    "티켓 조회",
    "티켓 목록",
)


class RequestRouter:
    """Current-message-only grammar.

    Thread history may provide a selected project later, but cannot turn a
    current code request into a Linear workspace operation.
    """

    def __init__(self, code_work_mode: Literal["analysis", "plan", "edit"] = "analysis") -> None:
        self._code_work_mode = code_work_mode

    def route(
        self,
        text: str,
        *,
        project_name: str | None = None,
    ) -> RoutedRequest:
        command = _MENTION.sub("", text).strip()
        normalized = command.casefold()
        command_line = normalized.splitlines()[0] if normalized else ""
        confirmation = _CONFIRM.fullmatch(command)
        if confirmation:
            return RoutedRequest(
                RequestIntent.CONTROL_CONFIRM,
                command,
                project_name,
                confirmation[1],
            )
        if _CANCEL.fullmatch(command):
            return RoutedRequest(RequestIntent.CONTROL_CANCEL, command, project_name, "취소")
        if _DISCARD.fullmatch(command):
            return RoutedRequest(RequestIntent.CODE_WORK, command, project_name)
        plan_confirmation = _PLAN_CONFIRM.fullmatch(command)
        if plan_confirmation:
            return RoutedRequest(
                RequestIntent.PLAN_CONFIRM,
                command,
                project_name,
                area=plan_confirmation[1].lower(),
            )

        if _is_thread_summary_line(command_line) and not any(
            marker in command_line for marker in _FEATURE_QUESTION_MARKERS
        ):
            return RoutedRequest(RequestIntent.THREAD_SUMMARY, command, project_name)

        # Fixed Linear commands only: the first line carries the command; later lines
        # are ticket fields whose text may hold any vocabulary.
        if not any(marker in command_line for marker in _FEATURE_QUESTION_MARKERS):
            if _starts_with_linear_command(command_line, _LINEAR_CREATE + _LINEAR_UPDATE):
                return RoutedRequest(RequestIntent.LINEAR_MUTATION, command, project_name)
            if _starts_with_linear_command(command_line, _LINEAR_READ):
                return RoutedRequest(RequestIntent.LINEAR_READ, command, project_name)

        # Everything else follows the configured mode (`CODE_WORK_MODE`), never the wording.
        if self._code_work_mode == "analysis":
            return RoutedRequest(RequestIntent.PROJECT_ANALYSIS, command, project_name)
        return RoutedRequest(RequestIntent.CODE_WORK, command, project_name, mode_decided=True)


def _is_thread_summary_line(command_line: str) -> bool:
    return ("스레드" in command_line and "요약" in command_line) or "thread summary" in command_line


def _starts_with_linear_command(command_line: str, markers: tuple[str, ...]) -> bool:
    return any(command_line.startswith(f"linear {marker}") for marker in markers)

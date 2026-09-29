"""Deterministic, typed routing before any LLM or external tool call."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from src.code_work_markers import CODE_INTEGRATION_MARKERS as _CODE_INTEGRATION_MARKERS
from src.code_work_markers import CODE_MARKERS as _CODE_MARKERS
from src.code_work_markers import CODE_PLANNING_MARKERS as _CODE_PLANNING_MARKERS
from src.code_work_markers import PLAN_CONTINUATION_MARKERS as _PLAN_CONTINUATION_MARKERS
from src.code_work_markers import is_plan_follow_work_request
from src.thread_context import ThreadWorkContext


class RequestIntent(StrEnum):
    CONTROL_CONFIRM = "control_confirm"
    CONTROL_CANCEL = "control_cancel"
    CODE_WORK = "code_work"
    ARTIFACT_GENERATION = "artifact_generation"
    LINEAR_READ = "linear_read"
    LINEAR_MUTATION = "linear_mutation"
    PROJECT_ANALYSIS = "project_analysis"
    SYSTEM_INQUIRY = "system_inquiry"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class RoutedRequest:
    intent: RequestIntent
    text: str
    project_name: str | None = None
    confirmation_verb: str | None = None
    llm_classified: bool = False


_MENTION = re.compile(r"<@[^>]+>")
_CONFIRM = re.compile(r"^(실행|실행해줘|실행합니다|저장|저장해줘|저장합니다)$")
_CANCEL = re.compile(r"^(취소|취소해줘|취소합니다)$")
_ARTIFACT_MARKERS = ("생성", "만들", "정리", "create", "write", "저장 위치")
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
_SYSTEM_MARKERS = ("mcp 연동", "mcp 연결", "mcp 가능한", "mcp 지원")
_PROJECT_CODE_CONTEXT = (
    "project", "프로젝트", "코드", "src/", "tests/", ".py", "pytest", "test",
    "plan.md", "개발", "구현", "연동 작업",
)
_INQUIRY_MARKERS = ("?", "？", "기반", "학습", "설계한", "설계됐", "지원", "가능")


class IntentClassifier(Protocol):
    def classify(self, text: str, thread_context: ThreadWorkContext | None) -> RequestIntent: ...


class RequestRouter:
    """Current-message-only grammar.

    Thread history may provide a selected project later, but cannot turn a
    current code request into a Linear workspace operation.
    """

    def __init__(self, intent_classifier: IntentClassifier | None = None) -> None:
        self._intent_classifier = intent_classifier

    def route(
        self,
        text: str,
        *,
        project_name: str | None = None,
        thread_context: ThreadWorkContext | None = None,
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

        # The first line carries the command; later lines can be ticket fields
        # whose title or description happen to contain code-work vocabulary.
        if _starts_with_linear_command(command_line, _LINEAR_CREATE + _LINEAR_UPDATE):
            if any(marker in command_line for marker in ("가능", "지원", "어떻게")):
                return RoutedRequest(RequestIntent.SYSTEM_INQUIRY, command, project_name)
            return RoutedRequest(RequestIntent.LINEAR_MUTATION, command, project_name)
        if _starts_with_linear_command(command_line, _LINEAR_READ):
            return RoutedRequest(RequestIntent.LINEAR_READ, command, project_name)
        if (
            "linear" in command_line
            and any(marker in command_line for marker in _PROJECT_CODE_CONTEXT)
            and any(marker in command_line for marker in _CODE_MARKERS + _CODE_PLANNING_MARKERS)
            and not any(marker in command_line for marker in ("가능", "지원", "학습", "기반"))
        ):
            return RoutedRequest(RequestIntent.CODE_WORK, command, project_name)
        if ("linear" in command_line or "graphql" in command_line) and any(
            marker in command_line for marker in _INQUIRY_MARKERS
        ):
            return RoutedRequest(RequestIntent.SYSTEM_INQUIRY, command, project_name)
        if is_plan_follow_work_request(command_line):
            return RoutedRequest(RequestIntent.CODE_WORK, command, project_name)
        if any(marker in command_line for marker in _CODE_INTEGRATION_MARKERS) or (
            "linear" in command_line
            and any(marker in command_line for marker in _PROJECT_CODE_CONTEXT)
        ):
            return RoutedRequest(RequestIntent.CODE_WORK, command, project_name)
        if any(
            marker in normalized
            for marker in _CODE_MARKERS + _CODE_PLANNING_MARKERS + _CODE_INTEGRATION_MARKERS
        ):
            return RoutedRequest(RequestIntent.CODE_WORK, command, project_name)
        if "linear" in normalized:
            return RoutedRequest(RequestIntent.SYSTEM_INQUIRY, command, project_name)
        if any(marker in normalized for marker in _ARTIFACT_MARKERS):
            return RoutedRequest(RequestIntent.ARTIFACT_GENERATION, command, project_name)
        if any(marker in normalized for marker in _SYSTEM_MARKERS):
            return RoutedRequest(RequestIntent.SYSTEM_INQUIRY, command, project_name)
        if (
            thread_context is not None
            and (thread_context.has_pending_plan or thread_context.last_intent_was_code_work)
            and any(marker in normalized for marker in _PLAN_CONTINUATION_MARKERS)
            and (
                project_name is None
                or thread_context.project_name is None
                or project_name == thread_context.project_name
            )
        ):
            return RoutedRequest(RequestIntent.CODE_WORK, command, project_name)
        if self._classified_as_code_work(command, thread_context):
            return RoutedRequest(
                RequestIntent.CODE_WORK, command, project_name, llm_classified=True
            )
        return RoutedRequest(RequestIntent.PROJECT_ANALYSIS, command, project_name)

    def _classified_as_code_work(
        self, command: str, thread_context: ThreadWorkContext | None
    ) -> bool:
        if self._intent_classifier is None:
            return False
        try:
            return self._intent_classifier.classify(command, thread_context) is (
                RequestIntent.CODE_WORK
            )
        except Exception:
            # An unavailable classifier must never break routing.
            return False


def _starts_with_linear_command(command_line: str, markers: tuple[str, ...]) -> bool:
    return any(command_line.startswith(f"linear {marker}") for marker in markers)

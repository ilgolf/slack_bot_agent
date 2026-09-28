"""Deterministic, typed routing before any LLM or external tool call."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


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


_MENTION = re.compile(r"<@[^>]+>")
_CONFIRM = re.compile(r"^(실행|실행해줘|실행합니다|저장|저장해줘|저장합니다)$")
_CANCEL = re.compile(r"^(취소|취소해줘|취소합니다)$")
_CODE_MARKERS = (
    "수정",
    "변경",
    "고쳐",
    "구현",
    "리팩터",
    "리팩토",
    "추가",
    "테스트",
    "test",
    "pytest",
    "ruff",
    "mypy",
    "린트",
    "타입 검사",
    "코드",
    "fix",
    "implement",
    "refactor",
    "update",
    "run test",
    "run lint",
    "typecheck",
)
_CODE_PLANNING_MARKERS = (
    "작업 plan",
    "작업 계획",
    "구현 계획",
    "계획 짜",
    "plan 짜",
    "plan 만들어",
    "설계해",
)
_CODE_INTEGRATION_MARKERS = ("github 연동", "gitlab 연동", "api 연동", "연동 작업")
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


class RequestRouter:
    """Current-message-only grammar.

    Thread history may provide a selected project later, but cannot turn a
    current code request into a Linear workspace operation.
    """

    def route(self, text: str, *, project_name: str | None = None) -> RoutedRequest:
        command = _MENTION.sub("", text).strip()
        normalized = command.casefold()
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

        # Project/code verbs always win over a product/domain word such as Linear.
        if any(
            marker in normalized
            for marker in _CODE_MARKERS + _CODE_PLANNING_MARKERS + _CODE_INTEGRATION_MARKERS
        ):
            return RoutedRequest(RequestIntent.CODE_WORK, command, project_name)
        if "linear" in normalized:
            if any(marker in normalized for marker in _LINEAR_CREATE + _LINEAR_UPDATE):
                return RoutedRequest(RequestIntent.LINEAR_MUTATION, command, project_name)
            if any(marker in normalized for marker in _LINEAR_READ):
                return RoutedRequest(RequestIntent.LINEAR_READ, command, project_name)
            return RoutedRequest(RequestIntent.SYSTEM_INQUIRY, command, project_name)
        if any(marker in normalized for marker in _ARTIFACT_MARKERS):
            return RoutedRequest(RequestIntent.ARTIFACT_GENERATION, command, project_name)
        if any(marker in normalized for marker in _SYSTEM_MARKERS):
            return RoutedRequest(RequestIntent.SYSTEM_INQUIRY, command, project_name)
        return RoutedRequest(RequestIntent.PROJECT_ANALYSIS, command, project_name)

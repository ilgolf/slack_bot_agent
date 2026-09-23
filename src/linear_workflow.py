"""Slack-facing Linear request routing with one-time confirmed mutations."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import Lock

from src.config import Settings
from src.linear_client import LinearApiError, LinearClient
from src.linear_tools import LinearIssue, LinearTeam, LinearTools
from src.thread_context import ThreadContextStore

logger = logging.getLogger(__name__)

_SLACK_MENTION = re.compile(r"<@[^>]+>")
_CONFIRMATION = re.compile(r"^(실행|실행해줘|실행합니다)$")
_CANCELLATION = re.compile(r"^(취소|취소해줘|취소합니다)$")
_ISSUE_IDENTIFIER = re.compile(r"\b([A-Za-z][A-Za-z0-9]*-\d+)\b")
_FIELD_PATTERNS = {
    "team_id": re.compile(r"팀\s*ID\s*[:：]\s*([^\s]+)", re.IGNORECASE),
    "title": re.compile(
        r"제목\s*[:：]\s*(.+?)(?=\n(?:설명|상태\s*ID|팀\s*ID)\s*[:：]|$)", re.DOTALL
    ),
    "description": re.compile(
        r"설명\s*[:：]\s*(.+?)(?=\n(?:제목|상태\s*ID|팀\s*ID)\s*[:：]|$)", re.DOTALL
    ),
    "state_id": re.compile(r"상태\s*ID\s*[:：]\s*([^\s]+)", re.IGNORECASE),
}
_CONNECTION_MARKERS = ("연결", "상태", "설정", "가능", "mcp")
_CREATE_MARKERS = ("이슈 생성", "티켓 생성", "이슈 추가", "티켓 추가", "이슈 만들", "티켓 만들")
_UPDATE_MARKERS = ("이슈 수정", "티켓 수정", "이슈 변경", "티켓 변경", "이슈 업데이트")
_TEAM_MARKERS = ("팀 조회", "팀 목록", "팀 리스트")
_ISSUE_MARKERS = ("이슈 조회", "이슈 목록", "티켓 조회", "티켓 목록")


class PendingLinearActionStatus(StrEnum):
    MISSING = "missing"
    READY = "ready"
    EXPIRED = "expired"


@dataclass(frozen=True)
class LinearActionDraft:
    """A typed mutation that has been previewed but not sent to Linear yet."""

    operation: str
    summary: str
    variables: dict[str, str]


@dataclass(frozen=True)
class PendingLinearAction:
    draft: LinearActionDraft
    created_at: datetime


class PendingLinearActionStore:
    """Thread-keyed ephemeral mutation drafts with single-consumption semantics."""

    def __init__(self, *, ttl: timedelta = timedelta(minutes=15)) -> None:
        self.ttl = ttl
        self._lock = Lock()
        self._actions: dict[tuple[str, str], PendingLinearAction] = {}

    def put(self, channel_id: str, thread_ts: str, draft: LinearActionDraft) -> None:
        with self._lock:
            self._actions[(channel_id, thread_ts)] = PendingLinearAction(
                draft=draft, created_at=datetime.now(UTC)
            )

    def take(
        self, channel_id: str, thread_ts: str
    ) -> tuple[PendingLinearActionStatus, PendingLinearAction | None]:
        with self._lock:
            action = self._actions.pop((channel_id, thread_ts), None)
        if action is None:
            return PendingLinearActionStatus.MISSING, None
        if datetime.now(UTC) - action.created_at > self.ttl:
            return PendingLinearActionStatus.EXPIRED, None
        return PendingLinearActionStatus.READY, action

    def cancel(self, channel_id: str, thread_ts: str) -> PendingLinearActionStatus:
        status, _ = self.take(channel_id, thread_ts)
        return status


class LinearIntegrationWorkflow:
    """Route fixed Linear commands before artifact generation or project analysis."""

    def __init__(
        self,
        *,
        settings: Settings,
        tools: LinearTools | None = None,
        action_store: PendingLinearActionStore | None = None,
    ) -> None:
        self.settings = settings
        self.tools = tools
        self.action_store = action_store or PendingLinearActionStore()

    def process(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        text: str,
        thread_context: ThreadContextStore,
        agent: object,
    ) -> str | None:
        """Handle a supported Linear request, returning ``None`` for other workflows.

        Read requests run immediately.  A write request produces a typed draft;
        only a later same-thread confirmation consumes and sends that draft.
        """
        del thread_context, agent
        command = _SLACK_MENTION.sub("", text).strip()
        if _CONFIRMATION.fullmatch(command):
            return self._execute_pending(channel_id, thread_ts)
        if _CANCELLATION.fullmatch(command):
            return self._cancel_pending(channel_id, thread_ts)
        if not _is_linear_request(command):
            return None
        if not self.settings.linear_api_key:
            return _missing_key_response()

        tools = self._tools()
        try:
            if _contains(command, _CREATE_MARKERS):
                return self._preview_create(channel_id, thread_ts, command)
            if _contains(command, _UPDATE_MARKERS):
                return self._preview_update(channel_id, thread_ts, command, tools)
            if _is_connection_request(command):
                return _render_connection(tools.list_teams())
            if _contains(command, _TEAM_MARKERS):
                return _render_teams(tools.list_teams())
            if _contains(command, _ISSUE_MARKERS):
                return self._read_issues(command, tools)
        except (LinearApiError, ValueError) as exc:
            logger.warning("linear_request_failed category=%s", type(exc).__name__)
            return f"Linear 작업을 완료하지 못했습니다: {exc}"
        return _help_response()

    def _tools(self) -> LinearTools:
        if self.tools is None:
            self.tools = LinearTools(
                LinearClient(
                    api_key=self.settings.linear_api_key or "",
                    api_url=self.settings.linear_api_url,
                    timeout_seconds=self.settings.linear_timeout_seconds,
                )
            )
        return self.tools

    def _read_issues(self, command: str, tools: LinearTools) -> str:
        identifier = _issue_identifier(command)
        if identifier:
            issue = tools.get_issue(identifier)
            if issue is None:
                return f"`{identifier}` 이슈를 찾지 못했습니다."
            return _render_issue(issue)
        return _render_issues(tools.list_issues())

    def _preview_create(self, channel_id: str, thread_ts: str, command: str) -> str:
        team_id = _field(command, "team_id")
        title = _field(command, "title")
        if team_id is None or title is None:
            return (
                "이슈 생성에는 `팀 ID`와 `제목`이 필요합니다.\n"
                "예시:\nLinear 이슈 생성\n팀 ID: <팀 UUID>\n제목: 로그인 오류 수정\n설명: 재현 절차"
            )
        description = _field(command, "description")
        variables = {"team_id": team_id, "title": title}
        if description is not None:
            variables["description"] = description
        draft = LinearActionDraft(
            operation="create_issue",
            summary=f"새 이슈 생성\n팀 ID: `{team_id}`\n제목: {title}"
            + (f"\n설명: {description}" if description else ""),
            variables=variables,
        )
        self.action_store.put(channel_id, thread_ts, draft)
        logger.info("linear_action_preview operation=create_issue")
        return _render_action_preview(draft)

    def _preview_update(
        self, channel_id: str, thread_ts: str, command: str, tools: LinearTools
    ) -> str:
        identifier = _issue_identifier(command)
        if identifier is None:
            return "이슈 수정에는 대상 식별자가 필요합니다. 예: `Linear 이슈 수정 ENG-123`"
        issue = tools.get_issue(identifier)
        if issue is None:
            return f"`{identifier}` 이슈를 찾지 못했습니다."
        variables = _update_fields(command)
        if not variables:
            return "이슈 수정에는 `제목`, `설명`, `상태 ID` 중 하나를 지정해 주세요."
        variables["issue_id"] = issue.id
        changes = "\n".join(
            f"{_field_label(key)}: {value}" for key, value in variables.items() if key != "issue_id"
        )
        draft = LinearActionDraft(
            operation="update_issue",
            summary=f"이슈 수정: `{issue.identifier}`\n{changes}",
            variables=variables,
        )
        self.action_store.put(channel_id, thread_ts, draft)
        logger.info("linear_action_preview operation=update_issue issue=%s", issue.identifier)
        return _render_action_preview(draft)

    def _execute_pending(self, channel_id: str, thread_ts: str) -> str | None:
        status, pending = self.action_store.take(channel_id, thread_ts)
        if status is PendingLinearActionStatus.MISSING:
            return None
        if status is PendingLinearActionStatus.EXPIRED:
            return "보류된 Linear 작업이 만료되었습니다. 새 요청으로 미리보기를 만들어 주세요."
        assert pending is not None
        try:
            issue = _run_action(self._tools(), pending.draft)
        except (LinearApiError, ValueError) as exc:
            logger.warning("linear_action_failed operation=%s", pending.draft.operation)
            return (
                f"Linear 변경에 실패했습니다: {exc}\n"
                "같은 작업은 자동 재시도하지 않았습니다. 새로 요청해 주세요."
            )
        logger.info(
            "linear_action_completed operation=%s issue=%s",
            pending.draft.operation,
            issue.identifier,
        )
        location = f"\n{issue.url}" if issue.url else ""
        verb = "생성" if pending.draft.operation == "create_issue" else "수정"
        return f"✅ Linear 이슈를 {verb}했습니다: `{issue.identifier}`{location}"

    def _cancel_pending(self, channel_id: str, thread_ts: str) -> str | None:
        status = self.action_store.cancel(channel_id, thread_ts)
        if status is PendingLinearActionStatus.MISSING:
            return None
        if status is PendingLinearActionStatus.EXPIRED:
            return "보류된 Linear 작업이 만료되었습니다."
        logger.info("linear_action_cancelled")
        return "보류된 Linear 작업을 취소했습니다. Linear에는 변경하지 않았습니다."


def _run_action(tools: LinearTools, draft: LinearActionDraft) -> LinearIssue:
    if draft.operation == "create_issue":
        return tools.create_issue(
            team_id=draft.variables["team_id"],
            title=draft.variables["title"],
            description=draft.variables.get("description"),
        )
    if draft.operation == "update_issue":
        return tools.update_issue(
            issue_id=draft.variables["issue_id"],
            title=draft.variables.get("title"),
            description=draft.variables.get("description"),
            state_id=draft.variables.get("state_id"),
        )
    raise ValueError("허용되지 않은 Linear 작업입니다")


def _is_linear_request(command: str) -> bool:
    return "linear" in command.casefold() and (
        _contains(command, _CONNECTION_MARKERS)
        or _contains(command, _CREATE_MARKERS)
        or _contains(command, _UPDATE_MARKERS)
        or _contains(command, _TEAM_MARKERS)
        or _contains(command, _ISSUE_MARKERS)
    )


def _is_connection_request(command: str) -> bool:
    return "linear" in command.casefold() and _contains(command, _CONNECTION_MARKERS)


def _contains(command: str, markers: tuple[str, ...]) -> bool:
    normalized = command.casefold()
    return any(marker.casefold() in normalized for marker in markers)


def _field(command: str, name: str) -> str | None:
    match = _FIELD_PATTERNS[name].search(command)
    return match.group(1).strip() if match and match.group(1).strip() else None


def _update_fields(command: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key in ("title", "description", "state_id"):
        value = _field(command, key)
        if value is not None:
            fields[key] = value
    return fields


def _issue_identifier(command: str) -> str | None:
    match = _ISSUE_IDENTIFIER.search(command)
    return match.group(1) if match else None


def _field_label(name: str) -> str:
    return {"title": "제목", "description": "설명", "state_id": "상태 ID"}[name]


def _missing_key_response() -> str:
    return (
        "Linear가 아직 설정되지 않았습니다. 이 PC의 `.env`에 `LINEAR_API_KEY`를 추가한 뒤 "
        "봇을 다시 시작하세요. Personal API Key는 Linear Settings → Security & Access → "
        "Personal API keys에서 발급합니다."
    )


def _render_connection(teams: list[LinearTeam]) -> str:
    return f"✅ Linear API가 연결되었습니다. 접근 가능한 팀: {len(teams)}개"


def _render_teams(teams: list[LinearTeam]) -> str:
    if not teams:
        return "조회 가능한 Linear 팀이 없습니다."
    return "Linear 팀:\n" + "\n".join(
        f"- `{team.key}` · {team.name} (ID: `{team.id}`)" for team in teams
    )


def _render_issues(issues: list[LinearIssue]) -> str:
    if not issues:
        return "조회할 Linear 이슈가 없습니다."
    return "Linear 이슈:\n" + "\n".join(
        f"- `{issue.identifier}` {issue.title}"
        + (f" · {issue.state_name}" if issue.state_name else "")
        for issue in issues
    )


def _render_issue(issue: LinearIssue) -> str:
    details = [f"`{issue.identifier}` {issue.title}"]
    if issue.team_name:
        details.append(f"팀: {issue.team_name}")
    if issue.state_name:
        details.append(f"상태: {issue.state_name}")
    if issue.description:
        details.append(f"설명: {issue.description}")
    if issue.url:
        details.append(issue.url)
    return "\n".join(details)


def _render_action_preview(draft: LinearActionDraft) -> str:
    return (
        "Linear 변경 초안을 만들었습니다.\n"
        f"{draft.summary}\n\n"
        "내용을 확인한 뒤 같은 스레드에 `실행`이라고 보내면 적용합니다. `취소`하면 폐기합니다."
    )


def _help_response() -> str:
    return (
        "지원 명령: `Linear 연결 상태 확인`, `Linear 팀 조회`, `Linear 이슈 조회`, "
        "`Linear 이슈 생성`, `Linear 이슈 수정 ENG-123`"
    )

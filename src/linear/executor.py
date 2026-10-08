"""E: 확인된 쓰기 실행 — 미리본 초안을 한 번만 꺼내 Linear로 보낸다 (plan.md Phase 32)."""


from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import Lock

from src.linear.tooluse import LinearIssue, LinearTools


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

    def has_pending(self, channel_id: str, thread_ts: str) -> bool:
        with self._lock:
            action = self._actions.get((channel_id, thread_ts))
            if action is None:
                return False
            if datetime.now(UTC) - action.created_at > self.ttl:
                self._actions.pop((channel_id, thread_ts), None)
                return False
            return True


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

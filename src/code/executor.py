"""E: 사용자 승인 대기 계획·프로젝트 되묻기 저장소 (plan.md Phase 32)."""


from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import Lock

from src.code.plan import ExecutionPlan, _fingerprint


class PendingPlanStatus(StrEnum):
    MISSING = "missing"
    READY = "ready"
    EXPIRED = "expired"


@dataclass(frozen=True)
class PendingPlan:
    plan: ExecutionPlan
    created_at: datetime
    fingerprint: str


class PendingPlanStore:
    """Thread-keyed plan storage.  Pending plans are deliberately not persistent."""

    def __init__(self, *, ttl: timedelta = timedelta(minutes=15)) -> None:
        self.ttl = ttl
        self._lock = Lock()
        self._plans: dict[tuple[str, str], PendingPlan] = {}

    def put(
        self, channel_id: str, thread_ts: str, plan: ExecutionPlan, *, request: str
    ) -> PendingPlan:
        fingerprint = _fingerprint(request)
        key = (channel_id, thread_ts)
        with self._lock:
            existing = self._plans.get(key)
            if (
                existing is not None
                and not self._expired(existing)
                and existing.fingerprint == fingerprint
            ):
                return existing
            pending = PendingPlan(plan=plan, created_at=datetime.now(UTC), fingerprint=fingerprint)
            self._plans[key] = pending
            return pending

    def take(self, channel_id: str, thread_ts: str) -> tuple[PendingPlanStatus, PendingPlan | None]:
        key = (channel_id, thread_ts)
        with self._lock:
            pending = self._plans.pop(key, None)
            if pending is None:
                return PendingPlanStatus.MISSING, None
            if self._expired(pending):
                return PendingPlanStatus.EXPIRED, None
            return PendingPlanStatus.READY, pending

    def cancel(self, channel_id: str, thread_ts: str) -> PendingPlanStatus:
        status, _ = self.take(channel_id, thread_ts)
        return status

    def has_pending(self, channel_id: str, thread_ts: str) -> bool:
        """Check a non-expired draft without consuming its confirmation."""
        with self._lock:
            pending = self._plans.get((channel_id, thread_ts))
            if pending is None:
                return False
            if self._expired(pending):
                self._plans.pop((channel_id, thread_ts), None)
                return False
            return True

    def peek(self, channel_id: str, thread_ts: str) -> ExecutionPlan | None:
        """Read a non-expired draft's plan without consuming its confirmation
        — for a clarification message that describes what's pending."""
        with self._lock:
            pending = self._plans.get((channel_id, thread_ts))
            if pending is None:
                return None
            if self._expired(pending):
                self._plans.pop((channel_id, thread_ts), None)
                return None
            return pending.plan

    def _expired(self, pending: PendingPlan) -> bool:
        return datetime.now(UTC) - pending.created_at > self.ttl


class AwaitingProjectStore:
    """Remembers a code-work request that stalled only for lack of a project
    name, so the very next reply supplying just the name resumes it instead
    of being judged as a fresh, markerless message. Thread context already
    carries the original request text — this only needs to remember *that*
    a request is waiting, TTL-bound like `PendingPlanStore`."""

    def __init__(self, *, ttl: timedelta = timedelta(minutes=15)) -> None:
        self.ttl = ttl
        self._lock = Lock()
        self._marked_at: dict[tuple[str, str], datetime] = {}

    def mark(self, channel_id: str, thread_ts: str) -> None:
        with self._lock:
            self._marked_at[(channel_id, thread_ts)] = datetime.now(UTC)

    def clear(self, channel_id: str, thread_ts: str) -> None:
        with self._lock:
            self._marked_at.pop((channel_id, thread_ts), None)

    def has_pending(self, channel_id: str, thread_ts: str) -> bool:
        with self._lock:
            marked_at = self._marked_at.get((channel_id, thread_ts))
            if marked_at is None:
                return False
            if datetime.now(UTC) - marked_at > self.ttl:
                self._marked_at.pop((channel_id, thread_ts), None)
                return False
            return True

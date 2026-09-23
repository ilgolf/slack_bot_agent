"""In-memory idempotency and lifecycle state for Slack analysis events."""

from __future__ import annotations

from enum import StrEnum
from threading import Lock


class ThreadRunState(StrEnum):
    IDLE = "idle"
    PLANNING = "planning"
    RUNNING = "running"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"


class ThreadRunStore:
    """Tracks event IDs once while keeping the latest state for each Slack thread."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._seen_event_ids: set[str] = set()
        self._states: dict[tuple[str, str], ThreadRunState] = {}

    def begin(self, *, event_id: str, channel_id: str, thread_ts: str) -> bool:
        with self._lock:
            if event_id in self._seen_event_ids:
                return False
            self._seen_event_ids.add(event_id)
            self._states[(channel_id, thread_ts)] = ThreadRunState.PLANNING
            return True

    def set_running(self, *, channel_id: str, thread_ts: str) -> None:
        self._set_state(channel_id, thread_ts, ThreadRunState.RUNNING)

    def complete(self, *, channel_id: str, thread_ts: str) -> None:
        self._set_state(channel_id, thread_ts, ThreadRunState.COMPLETED)

    def fail(self, *, channel_id: str, thread_ts: str) -> None:
        self._set_state(channel_id, thread_ts, ThreadRunState.FAILED)

    def state(self, *, channel_id: str, thread_ts: str) -> ThreadRunState:
        with self._lock:
            return self._states.get((channel_id, thread_ts), ThreadRunState.IDLE)

    def _set_state(self, channel_id: str, thread_ts: str, state: ThreadRunState) -> None:
        with self._lock:
            self._states[(channel_id, thread_ts)] = state

"""Persists a thread's conversation as a markdown file.

One file per (channel_id, thread_ts), append-only — read back to build LLM context
without re-fetching the thread from Slack every time (see plan.md's design summary).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ThreadWorkContext:
    """A thread's recent code-work state, read by the router to disambiguate
    a plan-continuation phrase from a plain analysis request."""

    has_pending_plan: bool = False
    project_name: str | None = None
    last_intent_was_code_work: bool = False


class ThreadContextStore:
    def __init__(self, *, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, channel_id: str, thread_ts: str) -> Path:
        return self.root / channel_id / f"{thread_ts}.md"

    def append(self, channel_id: str, thread_ts: str, message: str) -> None:
        path = self._path(channel_id, thread_ts)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(message + "\n")

    def read(self, channel_id: str, thread_ts: str) -> str:
        path = self._path(channel_id, thread_ts)
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")

    def _user_path(self, channel_id: str, thread_ts: str) -> Path:
        return self.root / channel_id / f"{thread_ts}.user.jsonl"

    def append_user_message(self, channel_id: str, thread_ts: str, message: str) -> None:
        """Record a message the user wrote, apart from the context text that also holds
        bot replies and data the bot read."""
        path = self._user_path(channel_id, thread_ts)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(message, ensure_ascii=False) + "\n")

    def user_messages(self, channel_id: str, thread_ts: str) -> list[str]:
        path = self._user_path(channel_id, thread_ts)
        if not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

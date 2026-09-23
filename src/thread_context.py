"""Persists a thread's conversation as a markdown file.

One file per (channel_id, thread_ts), append-only — read back to build LLM context
without re-fetching the thread from Slack every time (see plan.md's design summary).
"""

from __future__ import annotations

from pathlib import Path


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

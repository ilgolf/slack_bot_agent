"""ThreadContextStore: persists a thread's conversation as a markdown file.

One file per (channel_id, thread_ts), append-only — read back to build LLM context
without re-fetching the thread from Slack every time (see plan.md's design summary).
"""

from __future__ import annotations

from pathlib import Path

from src.thread_context import ThreadContextStore


def test_append_writes_message_to_thread_file(tmp_path: Path) -> None:
    store = ThreadContextStore(root=tmp_path)

    store.append("C123", "168000.0001", "hello")

    content = (tmp_path / "C123" / "168000.0001.md").read_text()
    assert "hello" in content


def test_append_does_not_overwrite_previous_messages(tmp_path: Path) -> None:
    store = ThreadContextStore(root=tmp_path)

    store.append("C123", "168000.0001", "first")
    store.append("C123", "168000.0001", "second")

    content = (tmp_path / "C123" / "168000.0001.md").read_text()
    assert "first" in content
    assert "second" in content


def test_read_returns_recorded_content(tmp_path: Path) -> None:
    store = ThreadContextStore(root=tmp_path)
    store.append("C123", "168000.0001", "hello")

    assert store.read("C123", "168000.0001") == "hello\n"


def test_read_returns_empty_string_when_nothing_recorded(tmp_path: Path) -> None:
    store = ThreadContextStore(root=tmp_path)

    assert store.read("C999", "000.0001") == ""

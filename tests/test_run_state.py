"""Slack event idempotency and thread lifecycle state."""

from __future__ import annotations

from src.run_state import ThreadRunState, ThreadRunStore


def test_run_store_accepts_an_event_once_and_tracks_completion() -> None:
    store = ThreadRunStore()

    assert store.begin(event_id="E1", channel_id="C1", thread_ts="1.1")
    assert not store.begin(event_id="E1", channel_id="C1", thread_ts="1.1")
    assert store.state(channel_id="C1", thread_ts="1.1") is ThreadRunState.PLANNING

    store.set_running(channel_id="C1", thread_ts="1.1")
    store.complete(channel_id="C1", thread_ts="1.1")

    assert store.state(channel_id="C1", thread_ts="1.1") is ThreadRunState.COMPLETED

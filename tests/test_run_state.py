"""Slack event idempotency and thread lifecycle state."""

from __future__ import annotations

from src.run_state import CodeWorkState, CodeWorkStateStore, ThreadRunState, ThreadRunStore


def test_run_store_accepts_an_event_once_and_tracks_completion() -> None:
    store = ThreadRunStore()

    assert store.begin(event_id="E1", channel_id="C1", thread_ts="1.1")
    assert not store.begin(event_id="E1", channel_id="C1", thread_ts="1.1")
    assert store.state(channel_id="C1", thread_ts="1.1") is ThreadRunState.PLANNING

    store.set_running(channel_id="C1", thread_ts="1.1")
    store.complete(channel_id="C1", thread_ts="1.1")

    assert store.state(channel_id="C1", thread_ts="1.1") is ThreadRunState.COMPLETED


def test_code_work_state_is_isolated_per_slack_thread() -> None:
    store = CodeWorkStateStore()

    store.set(channel_id="C1", thread_ts="1.1", state=CodeWorkState.DISCOVERING)
    store.set(channel_id="C1", thread_ts="2.2", state=CodeWorkState.AWAITING_CONFIRMATION)

    assert store.state(channel_id="C1", thread_ts="1.1") is CodeWorkState.DISCOVERING
    assert store.state(channel_id="C1", thread_ts="2.2") is CodeWorkState.AWAITING_CONFIRMATION

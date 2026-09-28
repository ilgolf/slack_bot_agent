"""ThreadTraceStore: per-thread trace storage so concurrent/sequential Slack
threads sharing one agent instance never see each other's trace."""

from __future__ import annotations

from src.agent_trace import AgentTraceRecorder, ThreadTraceStore


def test_thread_trace_store_returns_none_for_unknown_thread() -> None:
    store = ThreadTraceStore()

    assert store.get("C1", "T1") is None


def test_thread_trace_store_returns_the_trace_put_for_that_thread() -> None:
    store = ThreadTraceStore()
    trace = AgentTraceRecorder(request_id="req-1")

    store.put("C1", "T1", trace)

    assert store.get("C1", "T1") is trace

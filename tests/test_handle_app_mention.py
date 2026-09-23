"""handle_app_mention: runs dispatch_command from a Slack app_mention event and
replies in-thread via `say`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.agent import FakeAnalysisAgent
from src.config import Settings
from src.dispatch import render_result
from src.linear_workflow import LinearIntegrationWorkflow
from src.run_state import ThreadRunState, ThreadRunStore
from src.slack_app import handle_app_mention
from src.thread_context import ThreadContextStore


class RecordingSay:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def test_handle_app_mention_replies_in_thread(tmp_path: Path) -> None:
    thread_context = ThreadContextStore(root=tmp_path / "context")
    agent = FakeAnalysisAgent()
    say = RecordingSay()
    text = "이 프로젝트 뭐 하는 거야?"

    handle_app_mention(
        {"channel": "C1", "ts": "1.1", "text": text},
        say,
        thread_context=thread_context,
        agent=agent,
    )

    assert len(say.calls) == 2
    assert say.calls[0] == {"text": "분석 중입니다…", "thread_ts": "1.1"}
    assert say.calls[1]["thread_ts"] == "1.1"
    expected = render_result(agent.analyze(text))
    assert say.calls[1]["text"] == expected


def test_handle_app_mention_ignores_a_duplicate_event(tmp_path: Path) -> None:
    thread_context = ThreadContextStore(root=tmp_path / "context")
    say = RecordingSay()
    run_store = ThreadRunStore()
    event = {"channel": "C1", "ts": "1.1", "text": "분석해줘", "event_id": "E1"}

    for _ in range(2):
        handle_app_mention(
            event,
            say,
            thread_context=thread_context,
            agent=FakeAnalysisAgent(),
            run_store=run_store,
        )

    assert len(say.calls) == 2
    assert run_store.state(channel_id="C1", thread_ts="1.1") is ThreadRunState.COMPLETED


def test_linear_request_bypasses_project_analysis_when_not_configured(tmp_path: Path) -> None:
    say = RecordingSay()
    handle_app_mention(
        {"channel": "C1", "ts": "1.1", "text": "Linear 연결 상태 확인"},
        say,
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=FakeAnalysisAgent(),
        linear_workflow=LinearIntegrationWorkflow(settings=Settings(linear_api_key=None)),
    )

    assert "LINEAR_API_KEY" in say.calls[1]["text"]
    assert "가짜 분석" not in say.calls[1]["text"]

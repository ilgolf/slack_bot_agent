"""handle_app_mention: routes a Slack app_mention event through the coordinator and
replies in-thread via `say`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.code.agent import FakeAnalysisAgent
from src.code.workflow import ExecutionWorkflow
from src.core.config import Settings
from src.core.project_resolver import ProjectResolver
from src.core.run_state import ThreadRunState, ThreadRunStore
from src.linear.workflow import LinearIntegrationWorkflow
from src.slack.dispatch import render_result
from src.slack.request_coordinator import RequestCoordinator
from src.slack.slack_app import handle_app_mention
from src.slack.thread_context import ThreadContextStore
from tests.router_doubles import WordGatedRouter


def _coordinator(
    tmp_path: Path, *, linear_workflow: LinearIntegrationWorkflow | None = None
) -> RequestCoordinator:
    return RequestCoordinator(
        router=WordGatedRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
        linear_workflow=linear_workflow
        or LinearIntegrationWorkflow(settings=Settings(linear_api_key=None)),
    )


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
        coordinator=_coordinator(tmp_path),
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
            coordinator=_coordinator(tmp_path),
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
        coordinator=_coordinator(tmp_path),
    )

    assert "LINEAR_API_KEY" in say.calls[1]["text"]
    assert "가짜 분석" not in say.calls[1]["text"]


class FakeSlackClient:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    def chat_update(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)


def test_autopilot_progress_updates_the_initial_status_message(tmp_path: Path) -> None:
    from tests.code.test_autopilot import SequencedPlanningAgent, _write_plan

    project = tmp_path / "my-project"
    project.mkdir()
    (project / "plan.md").write_text("- [ ] first\n- [ ] second\n")
    coordinator = RequestCoordinator(
        router=WordGatedRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
        linear_workflow=LinearIntegrationWorkflow(settings=Settings(linear_api_key=None)),
    )
    agent = SequencedPlanningAgent(
        [_write_plan("src/a.txt", "A\n"), _write_plan("src/b.txt", "B\n")]
    )
    client = FakeSlackClient()

    class SayWithTs(RecordingSay):
        def __call__(self, **kwargs: Any) -> dict[str, str]:  # type: ignore[override]
            super().__call__(**kwargs)
            return {"ts": "2.2"}

    handle_app_mention(
        {"channel": "C1", "ts": "1.1", "text": "my-project plan.md 기준으로 끝까지 진행해"},
        SayWithTs(),
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,  # type: ignore[arg-type]
        coordinator=coordinator,
        client=client,
    )

    assert client.updates == [
        {"channel": "C1", "ts": "2.2", "text": "🔄 자동 진행 1/2 완료"},
        {"channel": "C1", "ts": "2.2", "text": "🔄 자동 진행 2/2 완료"},
    ]


def test_thread_summary_request_shows_a_thread_summary_status(tmp_path: Path) -> None:
    from src.slack.slack_thread import ThreadMessage
    from src.slack.thread_summary_workflow import ThreadSummaryWorkflow

    class OneMessageReader:
        def read(self, channel_id: str, thread_ts: str) -> list[ThreadMessage]:
            return [ThreadMessage(user="U1", ts="1790905490.0", text="안녕")]

    class FixedSummarizer:
        def summarize(self, transcript: str) -> str:
            return "요약"

    coordinator = RequestCoordinator(
        router=WordGatedRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
        linear_workflow=LinearIntegrationWorkflow(settings=Settings(linear_api_key=None)),
        thread_summary_workflow=ThreadSummaryWorkflow(
            reader=OneMessageReader(), summarizer=FixedSummarizer()
        ),
    )
    say = RecordingSay()

    handle_app_mention(
        {"channel": "C1", "ts": "2.0", "thread_ts": "1.0", "text": "<@U9> 이 스레드 요약해줘"},
        say,
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=FakeAnalysisAgent(),
        coordinator=coordinator,
    )

    assert say.calls[0] == {"text": "스레드 요약 중입니다…", "thread_ts": "1.0"}
    assert "1개 메시지를 요약했습니다" in say.calls[1]["text"]

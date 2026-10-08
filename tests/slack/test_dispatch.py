"""dispatch_command: forwards every non-empty mention straight to the analysis
agent — no fixed-grammar gate — recording thread context and translating
`AnalysisAgentError` into a clear reply (see plan.md Section 8).
"""

from __future__ import annotations

from pathlib import Path

from src.code.agent import AnalysisAgentError, AnalysisResult, FakeAnalysisAgent
from src.slack.dispatch import dispatch_command, render_result
from src.slack.thread_context import ThreadContextStore


class RecordingAgent:
    def __init__(self) -> None:
        self.questions: list[str] = []
        self.thread_keys: list[tuple[str, str]] = []

    def analyze(
        self, question: str, *, channel_id: str = "-", thread_ts: str = "-"
    ) -> AnalysisResult:
        self.questions.append(question)
        self.thread_keys.append((channel_id, thread_ts))
        return AnalysisResult(summary="요약", findings=[])


def test_dispatch_command_forwards_text_to_agent_and_records_thread_context(
    tmp_path: Path,
) -> None:
    thread_context = ThreadContextStore(root=tmp_path / "context")
    agent = FakeAnalysisAgent()
    text = "이 프로젝트 뭐 하는 거야?"

    response = dispatch_command(
        "C123",
        "168000.0001",
        text,
        thread_context=thread_context,
        agent=agent,
    )

    expected = render_result(agent.analyze(text))
    assert response == expected

    recorded = thread_context.read("C123", "168000.0001")
    assert text in recorded
    assert response in recorded


def test_dispatch_command_ignores_empty_text(tmp_path: Path) -> None:
    thread_context = ThreadContextStore(root=tmp_path / "context")
    agent = FakeAnalysisAgent()

    response = dispatch_command(
        "C123",
        "168000.0001",
        "   ",
        thread_context=thread_context,
        agent=agent,
    )

    assert response == ""


def test_dispatch_command_passes_prior_thread_context_to_agent(tmp_path: Path) -> None:
    thread_context = ThreadContextStore(root=tmp_path / "context")
    thread_context.append("C123", "168000.0001", "my-project이 있는지 확인해줘")
    agent = RecordingAgent()

    dispatch_command(
        "C123",
        "168000.0001",
        "README.md를 요약해줘",
        thread_context=thread_context,
        agent=agent,
    )

    assert agent.questions == [
        "스레드 맥락:\nmy-project이 있는지 확인해줘\n\n현재 요청:\nREADME.md를 요약해줘"
    ]


def test_dispatch_command_forwards_channel_and_thread_to_agent(tmp_path: Path) -> None:
    """The agent needs the caller's thread identity for a future thread-scoped
    trace store — dispatch already has both, so it must hand them through."""
    thread_context = ThreadContextStore(root=tmp_path / "context")
    agent = RecordingAgent()

    dispatch_command(
        "C123",
        "168000.0001",
        "이 프로젝트 뭐 하는 거야?",
        thread_context=thread_context,
        agent=agent,
    )

    assert agent.thread_keys == [("C123", "168000.0001")]


class BoomAgent:
    def analyze(
        self, question: str, *, channel_id: str = "-", thread_ts: str = "-"
    ) -> AnalysisResult:
        del channel_id, thread_ts
        raise AnalysisAgentError("모델 응답 파싱 실패")


def test_dispatch_command_replies_with_failure_message_on_analysis_agent_error(
    tmp_path: Path,
) -> None:
    thread_context = ThreadContextStore(root=tmp_path / "context")
    agent = BoomAgent()

    response = dispatch_command(
        "C123",
        "168000.0001",
        "무슨 파일이 있어?",
        thread_context=thread_context,
        agent=agent,
    )

    assert "작업 실패" in response
    assert "모델 응답 파싱 실패" in response

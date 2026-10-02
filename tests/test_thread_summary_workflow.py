"""ThreadSummaryWorkflow + coordinator routing (plan.md Phase 13, section E)."""

from __future__ import annotations

from pathlib import Path

from src.agent import FakeAnalysisAgent
from src.config import Settings
from src.execution_workflow import ExecutionWorkflow
from src.linear_workflow import LinearIntegrationWorkflow
from src.project_resolver import ProjectResolver
from src.request_coordinator import RequestCoordinator
from src.request_router import RequestIntent, RequestRouter
from src.slack_thread import ThreadMessage, ThreadReadError
from src.thread_context import ThreadContextStore
from src.thread_summarizer import ThreadSummaryError
from src.thread_summary_workflow import ThreadSummaryWorkflow


class FakeReader:
    def __init__(self, messages: list[ThreadMessage] | None = None, error: Exception | None = None):
        self.messages = messages or []
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def read(self, channel_id: str, thread_ts: str) -> list[ThreadMessage]:
        self.calls.append((channel_id, thread_ts))
        if self.error is not None:
            raise self.error
        return self.messages


class FakeSummarizer:
    def __init__(self, summary: str = "결정: 금요일 배포", error: Exception | None = None) -> None:
        self.summary = summary
        self.error = error
        self.transcripts: list[str] = []

    def summarize(self, transcript: str) -> str:
        self.transcripts.append(transcript)
        if self.error is not None:
            raise self.error
        return self.summary


def _messages(count: int) -> list[ThreadMessage]:
    return [ThreadMessage(user="U1", ts=f"{1790905490 + i}.0", text=f"m{i}") for i in range(count)]


def test_process_summarizes_the_thread_and_reports_the_message_count() -> None:
    reader = FakeReader(_messages(3))
    summarizer = FakeSummarizer()
    workflow = ThreadSummaryWorkflow(reader=reader, summarizer=summarizer)

    response = workflow.process(channel_id="C1", thread_ts="1.0")

    assert reader.calls == [("C1", "1.0")]
    assert "m0" in summarizer.transcripts[0]
    assert response.startswith("결정: 금요일 배포")
    assert "3개 메시지를 요약했습니다" in response


def test_process_reports_an_empty_thread_without_calling_the_summarizer() -> None:
    summarizer = FakeSummarizer()
    workflow = ThreadSummaryWorkflow(reader=FakeReader([]), summarizer=summarizer)

    response = workflow.process(channel_id="C1", thread_ts="1.0")

    assert response == "요약할 메시지가 없습니다."
    assert summarizer.transcripts == []


def test_process_appends_a_limitation_when_the_input_was_truncated() -> None:
    workflow = ThreadSummaryWorkflow(reader=FakeReader(_messages(250)), summarizer=FakeSummarizer())

    response = workflow.process(channel_id="C1", thread_ts="1.0")

    assert "한계" in response
    assert "200개 메시지를 요약했습니다" in response


def test_process_answers_read_and_summary_errors_with_guidance_not_exceptions() -> None:
    read_failure = ThreadSummaryWorkflow(
        reader=FakeReader(error=ThreadReadError("봇이 이 채널에 없습니다.")),
        summarizer=FakeSummarizer(),
    ).process(channel_id="C1", thread_ts="1.0")
    summary_failure = ThreadSummaryWorkflow(
        reader=FakeReader(_messages(1)),
        summarizer=FakeSummarizer(error=ThreadSummaryError("요약 시간이 초과되었습니다.")),
    ).process(channel_id="C1", thread_ts="1.0")

    assert read_failure == "봇이 이 채널에 없습니다."
    assert "요약 시간이 초과되었습니다." in summary_failure
    assert "❌" not in read_failure + summary_failure


def test_process_defangs_mentions_so_the_summary_does_not_notify_anyone() -> None:
    summarizer = FakeSummarizer(summary="<@U123> 가 <!channel> 에 공지, <!here> 확인")
    workflow = ThreadSummaryWorkflow(reader=FakeReader(_messages(1)), summarizer=summarizer)

    response = workflow.process(channel_id="C1", thread_ts="1.0")

    assert "<@" not in response
    assert "<!" not in response
    assert "@U123 가 @channel 에 공지, @here 확인" in response


def test_coordinator_routes_thread_summary_and_records_only_request_and_response(
    tmp_path: Path,
) -> None:
    summarizer = FakeSummarizer()
    coordinator = RequestCoordinator(
        router=RequestRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
        linear_workflow=LinearIntegrationWorkflow(settings=Settings(linear_api_key=None)),
        thread_summary_workflow=ThreadSummaryWorkflow(
            reader=FakeReader(_messages(2)), summarizer=summarizer
        ),
    )
    context = ThreadContextStore(root=tmp_path / "context")

    routed, response = coordinator.process(
        channel_id="C1",
        thread_ts="1.0",
        text="<@U9> 이 스레드 요약해줘",
        thread_context=context,
        agent=FakeAnalysisAgent(),
    )

    stored = context.read("C1", "1.0")
    assert routed.intent is RequestIntent.THREAD_SUMMARY
    assert "2개 메시지를 요약했습니다" in response
    assert response in stored
    assert "이 스레드 요약해줘" in stored
    assert "m0" not in stored and "m1" not in stored

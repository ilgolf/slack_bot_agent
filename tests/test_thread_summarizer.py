"""ThreadSummarizer implementations (plan.md Phase 13, section D). No test calls a
real LLM — fakes stand in."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, BaseMessage

from src.claude_sdk_runner import ClaudeSdkRunner
from src.code_agent_analysis import RunnerError, RunnerTimeout
from src.codex_sdk_runner import CodexSdkRunner
from src.config import Settings
from src.thread_summarizer import (
    FakeThreadSummarizer,
    LangChainThreadSummarizer,
    RunnerThreadSummarizer,
    ThreadSummaryError,
    build_summary_prompt,
    get_thread_summarizer,
)


def test_fake_summarizer_returns_a_fixed_summary() -> None:
    assert FakeThreadSummarizer().summarize("[09:00] U1: hi") == "가짜 스레드 요약입니다."


def test_summary_prompt_restricts_evidence_and_shape_and_ignores_bot_requests() -> None:
    prompt = build_summary_prompt("[09:00] U1: 배포는 금요일")

    assert "[09:00] U1: 배포는 금요일" in prompt
    assert "스레드 내용만" in prompt
    assert "한국어" in prompt
    for heading in ("결정", "할 일", "미해결"):
        assert heading in prompt
    assert "봇에게 한 요청" in prompt


class FakeChatModel:
    def __init__(self, reply: str = "요약 결과", error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.inputs: list[list[BaseMessage]] = []

    def invoke(self, input: list[BaseMessage]) -> AIMessage:
        self.inputs.append(input)
        if self.error is not None:
            raise self.error
        return AIMessage(content=self.reply)


def test_langchain_summarizer_sends_the_prompt_and_returns_the_reply_text() -> None:
    model = FakeChatModel(reply="  결정: 금요일 배포  ")

    summary = LangChainThreadSummarizer(chat_model=model).summarize("[09:00] U1: hi")

    assert summary == "결정: 금요일 배포"
    assert "[09:00] U1: hi" in str(model.inputs[0][0].content)


class FakeTextRunner:
    def __init__(self, reply: str = "러너 요약", error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.prompts: list[str] = []
        self.timeouts: list[float] = []

    def complete(self, prompt: str, *, timeout_seconds: float) -> str:
        self.prompts.append(prompt)
        self.timeouts.append(timeout_seconds)
        if self.error is not None:
            raise self.error
        return self.reply


def test_runner_summarizer_calls_the_text_runner_and_returns_its_text() -> None:
    runner = FakeTextRunner(reply="  러너 요약  ")

    summary = RunnerThreadSummarizer(runner=runner, timeout_seconds=42.0).summarize(
        "[09:00] U1: hi"
    )

    assert summary == "러너 요약"
    assert "[09:00] U1: hi" in runner.prompts[0]
    assert runner.timeouts == [42.0]


@pytest.mark.parametrize(
    ("error", "expected"),
    [(RunnerTimeout(), "시간이 초과"), (RunnerError("secret sk-x"), "실패")],
)
def test_runner_summarizer_turns_runner_errors_into_safe_messages(
    error: Exception, expected: str
) -> None:
    summarizer = RunnerThreadSummarizer(runner=FakeTextRunner(error=error), timeout_seconds=1.0)

    with pytest.raises(ThreadSummaryError) as exc_info:
        summarizer.summarize("x")

    assert expected in str(exc_info.value)
    assert "sk-x" not in str(exc_info.value)


def test_langchain_summarizer_turns_model_errors_into_safe_messages() -> None:
    summarizer = LangChainThreadSummarizer(
        chat_model=FakeChatModel(error=RuntimeError("secret sk-y"))
    )

    with pytest.raises(ThreadSummaryError) as exc_info:
        summarizer.summarize("x")

    assert "sk-y" not in str(exc_info.value)


def test_empty_summaries_are_reported_as_errors(tmp_path: Path) -> None:
    summarizer = RunnerThreadSummarizer(runner=FakeTextRunner(reply="  "), timeout_seconds=1.0)

    with pytest.raises(ThreadSummaryError):
        summarizer.summarize("x")


def test_get_thread_summarizer_returns_a_summarizer_per_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)  # no `.env` here
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/codex")
    settings = Settings(
        anthropic_api_key="sk-ant-fake", openai_api_key="sk-fake", agent_runner_timeout_seconds=42.0
    )

    assert isinstance(get_thread_summarizer("fake", settings=settings), FakeThreadSummarizer)
    assert isinstance(
        get_thread_summarizer("anthropic", settings=settings), LangChainThreadSummarizer
    )
    assert isinstance(get_thread_summarizer("openai", settings=settings), LangChainThreadSummarizer)
    claude = get_thread_summarizer("claude_code", settings=settings)
    codex = get_thread_summarizer("codex", settings=settings)
    assert isinstance(claude, RunnerThreadSummarizer)
    assert isinstance(claude._runner, ClaudeSdkRunner)
    assert claude._timeout_seconds == 42.0
    assert isinstance(codex, RunnerThreadSummarizer)
    assert isinstance(codex._runner, CodexSdkRunner)


def test_get_thread_summarizer_rejects_an_unknown_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)  # no `.env` here

    with pytest.raises(ValueError, match="unknown LLM provider"):
        get_thread_summarizer("nope", settings=Settings())

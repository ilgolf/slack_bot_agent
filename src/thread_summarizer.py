"""Summarize a Slack thread transcript with the configured provider (plan.md Phase 13).

The transcript leaves this process for an external LLM, so errors are turned into
fixed user-facing messages and never carry provider output.
"""

from __future__ import annotations

from typing import Any, Protocol

from langchain_core.messages import HumanMessage

from src.code_agent_analysis import RunnerError, RunnerTimeout
from src.config import Settings

_SUMMARY_PROMPT = (
    "다음은 Slack 스레드 대화입니다.\n"
    "- 스레드 내용만 근거로 요약하고, 없는 사실을 추가하지 마세요.\n"
    "- 한국어로 간결하게 쓰고, 결정 사항 / 할 일 / 미해결 질문 순서로 정리하세요. "
    "해당 내용이 없는 항목은 생략하세요.\n"
    "- 봇에게 한 요청 문장(요약해 달라는 말 등)은 요약에 포함하지 마세요.\n\n"
    "스레드:\n{transcript}"
)


class ThreadSummaryError(Exception):
    """The summary could not be produced; the message is safe to show in Slack."""


class ThreadSummarizer(Protocol):
    def summarize(self, transcript: str) -> str: ...


class TextRunner(Protocol):
    def complete(self, prompt: str, *, timeout_seconds: float) -> str: ...


class ChatModel(Protocol):
    def invoke(self, input: Any) -> Any: ...


def build_summary_prompt(transcript: str) -> str:
    return _SUMMARY_PROMPT.format(transcript=transcript)


def _non_empty(summary: str) -> str:
    cleaned = summary.strip()
    if not cleaned:
        raise ThreadSummaryError("요약 결과가 비어 있습니다.")
    return cleaned


class FakeThreadSummarizer:
    def summarize(self, transcript: str) -> str:
        del transcript
        return "가짜 스레드 요약입니다."


class LangChainThreadSummarizer:
    def __init__(self, *, chat_model: ChatModel) -> None:
        self._chat_model = chat_model

    def summarize(self, transcript: str) -> str:
        try:
            reply = self._chat_model.invoke(
                [HumanMessage(content=build_summary_prompt(transcript))]
            )
        except Exception:
            raise ThreadSummaryError("요약 중 오류가 발생했습니다.") from None
        return _non_empty(str(reply.content))


class RunnerThreadSummarizer:
    def __init__(self, *, runner: TextRunner, timeout_seconds: float) -> None:
        self._runner = runner
        self._timeout_seconds = timeout_seconds

    def summarize(self, transcript: str) -> str:
        try:
            text = self._runner.complete(
                build_summary_prompt(transcript), timeout_seconds=self._timeout_seconds
            )
        except RunnerTimeout:
            raise ThreadSummaryError("요약 시간이 초과되었습니다.") from None
        except RunnerError:
            raise ThreadSummaryError("요약 실행에 실패했습니다.") from None
        return _non_empty(text)


def get_thread_summarizer(provider: str, *, settings: Settings) -> ThreadSummarizer:
    """The same provider that answers analysis requests also summarizes threads."""
    if provider == "fake":
        return FakeThreadSummarizer()
    if provider in {"anthropic", "openai"}:
        from src.agent import build_chat_model

        return LangChainThreadSummarizer(chat_model=build_chat_model(provider, settings=settings))
    if provider in {"claude_code", "codex"}:
        from src.agent_runners import create_claude_runner, create_codex_runner

        runner = create_claude_runner() if provider == "claude_code" else create_codex_runner()
        return RunnerThreadSummarizer(
            runner=runner, timeout_seconds=settings.agent_runner_timeout_seconds
        )
    raise ValueError(f"unknown LLM provider: {provider!r}")

"""start_socket_mode wires the thread summary workflow (plan.md Phase 13, section F)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import src.slack_app as slack_app_module
from src.agent import FakeAnalysisAgent
from src.config import Settings
from src.slack_app import start_socket_mode
from src.thread_context import ThreadContextStore


class FakeSlackClient:
    def conversations_replies(self, **kwargs: Any) -> dict[str, Any]:
        return {"messages": [{"ts": "1790905490.0", "user": "U1", "text": "금요일에 배포해요"}]}

    def chat_update(self, **kwargs: Any) -> None:
        pass


class FakeBoltApp:
    def __init__(self) -> None:
        self.client = FakeSlackClient()
        self.handlers: dict[str, Callable[..., Any]] = {}

    def event(self, name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def register(handler: Callable[..., Any]) -> Callable[..., Any]:
            self.handlers[name] = handler
            return handler

        return register


class FixedSummarizer:
    def summarize(self, transcript: str) -> str:
        return f"요약 입력: {transcript}"


def test_socket_mode_answers_a_thread_summary_request_through_the_slack_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)  # no `.env` here
    bolt_app = FakeBoltApp()
    monkeypatch.setattr(slack_app_module, "build_slack_app", lambda settings: bolt_app)
    monkeypatch.setattr(
        slack_app_module,
        "SocketModeHandler",
        lambda app, token: type("Handler", (), {"start": lambda self: None})(),
    )
    start_socket_mode(
        Settings(slack_app_token="xapp-test"),
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=FakeAnalysisAgent(),
        thread_summarizer=FixedSummarizer(),
    )
    replies: list[dict[str, Any]] = []

    def say(**kwargs: Any) -> dict[str, str]:
        replies.append(kwargs)
        return {"ts": "9.9"}

    bolt_app.handlers["app_mention"](
        {"channel": "C1", "ts": "2.0", "thread_ts": "1.0", "text": "<@U9> 이 스레드 요약해줘"},
        say,
        bolt_app.client,
    )

    assert replies[0]["text"] == "스레드 요약 중입니다…"
    assert "금요일에 배포해요" in replies[1]["text"]
    assert "1개 메시지를 요약했습니다" in replies[1]["text"]

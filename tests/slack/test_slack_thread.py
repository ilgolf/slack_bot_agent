"""SlackThreadReader: reads the current thread through `conversations.replies`
(plan.md Phase 13, section B). No test calls Slack — a fake client stands in.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from slack_sdk.errors import SlackApiError

from src.slack.slack_thread import (
    SlackThreadReader,
    ThreadMessage,
    ThreadReadError,
    build_transcript,
)


class FakeSlackClient:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.calls: list[dict[str, Any]] = []

    def conversations_replies(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self.pages[len(self.calls) - 1]


def _message(ts: str, text: str, **extra: Any) -> dict[str, Any]:
    return {"ts": ts, "user": "U1", "text": text, **extra}


def test_read_returns_thread_messages_in_time_order() -> None:
    client = FakeSlackClient(
        [{"messages": [_message("1.0", "첫 글"), _message("2.0", "답글", user="U2")]}]
    )

    messages = SlackThreadReader(client).read("C1", "1.0")

    assert client.calls[0]["channel"] == "C1"
    assert client.calls[0]["ts"] == "1.0"
    assert messages == [
        ThreadMessage(user="U1", ts="1.0", text="첫 글"),
        ThreadMessage(user="U2", ts="2.0", text="답글"),
    ]


def test_read_skips_bot_messages() -> None:
    client = FakeSlackClient(
        [{"messages": [_message("1.0", "사람"), _message("2.0", "분석 중입니다…", bot_id="B1")]}]
    )

    assert [m.text for m in SlackThreadReader(client).read("C1", "1.0")] == ["사람"]


def test_read_follows_the_next_page_cursor() -> None:
    client = FakeSlackClient(
        [
            {"messages": [_message("1.0", "a")], "response_metadata": {"next_cursor": "abc"}},
            {"messages": [_message("2.0", "b")], "response_metadata": {"next_cursor": ""}},
        ]
    )

    messages = SlackThreadReader(client).read("C1", "1.0")

    assert [m.text for m in messages] == ["a", "b"]
    assert client.calls[1]["cursor"] == "abc"


def test_read_skips_system_and_empty_messages() -> None:
    client = FakeSlackClient(
        [
            {
                "messages": [
                    _message("1.0", "내용"),
                    _message("2.0", "<@U9> has joined the channel", subtype="channel_join"),
                    _message("3.0", "   "),
                ]
            }
        ]
    )

    assert [m.text for m in SlackThreadReader(client).read("C1", "1.0")] == ["내용"]


@pytest.mark.parametrize(
    ("slack_error", "hint"),
    [
        ("missing_scope", "history 권한"),
        ("not_in_channel", "채널에 초대"),
        ("channel_not_found", "채널에 초대"),
    ],
)
def test_read_turns_slack_errors_into_a_guiding_error_without_the_raw_response(
    slack_error: str, hint: str
) -> None:
    class FailingClient:
        def conversations_replies(self, **kwargs: Any) -> dict[str, Any]:
            raise SlackApiError("secret xoxb-token detail", {"error": slack_error})  # type: ignore[no-untyped-call]

    with pytest.raises(ThreadReadError) as exc_info:
        SlackThreadReader(FailingClient()).read("C1", "1.0")

    assert hint in str(exc_info.value)
    assert "xoxb-token" not in str(exc_info.value)


def _ts(hour: int, minute: int) -> str:
    return str(datetime(2026, 10, 2, hour, minute, tzinfo=UTC).timestamp())


def test_build_transcript_formats_one_line_per_message_with_time_and_author() -> None:
    messages = [
        ThreadMessage(user="U1", ts=_ts(9, 5), text="배포 언제 해요?"),
        ThreadMessage(user="U2", ts=_ts(9, 7), text="오늘 오후요"),
    ]

    transcript = build_transcript(messages, tz=UTC)

    assert transcript.text == "[09:05] U1: 배포 언제 해요?\n[09:07] U2: 오늘 오후요"
    assert transcript.message_count == 2
    assert not transcript.truncated


def test_build_transcript_keeps_only_the_most_recent_messages_over_the_count_limit() -> None:
    messages = [ThreadMessage(user="U1", ts=_ts(9, i), text=f"m{i}") for i in range(5)]

    transcript = build_transcript(messages, tz=UTC, max_messages=3)

    assert transcript.text.splitlines() == [
        "[09:02] U1: m2",
        "[09:03] U1: m3",
        "[09:04] U1: m4",
    ]
    assert transcript.message_count == 3
    assert transcript.truncated


def test_build_transcript_drops_the_oldest_messages_over_the_character_limit() -> None:
    messages = [ThreadMessage(user="U1", ts=_ts(9, i), text="x" * 20) for i in range(4)]

    transcript = build_transcript(messages, tz=UTC, max_chars=70)

    assert len(transcript.text) <= 70
    assert transcript.text.splitlines()[-1].startswith("[09:03]")
    assert transcript.message_count == len(transcript.text.splitlines()) < 4
    assert transcript.truncated

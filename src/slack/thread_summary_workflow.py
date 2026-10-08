"""Summarize the current Slack thread (plan.md Phase 13).

Thread text is read, summarized and discarded within one call: it is never logged,
traced or written to the thread context store.
"""

from __future__ import annotations

import re
from typing import Protocol

from src.slack.slack_thread import ThreadMessage, ThreadReadError, build_transcript
from src.slack.thread_summarizer import ThreadSummarizer, ThreadSummaryError

_MENTION = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")
_BROADCAST = re.compile(r"<!(channel|here|everyone)(?:\|[^>]*)?>")


class ThreadReader(Protocol):
    def read(self, channel_id: str, thread_ts: str) -> list[ThreadMessage]: ...


class ThreadSummaryWorkflow:
    def __init__(self, *, reader: ThreadReader, summarizer: ThreadSummarizer) -> None:
        self._reader = reader
        self._summarizer = summarizer

    def process(self, *, channel_id: str, thread_ts: str) -> str:
        try:
            messages = self._reader.read(channel_id, thread_ts)
            if not messages:
                return "요약할 메시지가 없습니다."
            transcript = build_transcript(messages)
            summary = self._summarizer.summarize(transcript.text)
        except (ThreadReadError, ThreadSummaryError) as exc:
            return str(exc)
        response = (
            f"{_defang_mentions(summary)}\n\n_{transcript.message_count}개 메시지를 요약했습니다._"
        )
        if transcript.truncated:
            response += (
                f"\n한계: 스레드가 길어 최근 {transcript.message_count}개 메시지만 요약했습니다."
            )
        return response


def _defang_mentions(text: str) -> str:
    """A bot reply containing `<@U123>` or `<!channel>` would notify real people."""
    return _BROADCAST.sub(r"@\1", _MENTION.sub(r"@\1", text))

"""Read the current Slack thread for summarization (plan.md Phase 13).

Only message text and author ids are kept in memory for the request; nothing here
logs or persists thread content.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any

from slack_sdk.errors import SlackApiError

_SYSTEM_SUBTYPES = frozenset(
    {"channel_join", "channel_leave", "channel_topic", "channel_purpose", "channel_name"}
)
_PAGE_LIMIT = 200
MAX_MESSAGES = 200
MAX_CHARS = 30_000
_ERROR_HINTS = {
    "missing_scope": "Slack 앱에 history 권한(channels:history 등)이 없습니다. "
    "권한을 추가하고 앱을 재설치해 주세요.",
    "not_in_channel": "봇이 이 채널에 없습니다. 채널에 초대한 뒤 다시 시도해 주세요.",
    "channel_not_found": "봇이 이 채널을 찾을 수 없습니다. 채널에 초대한 뒤 다시 시도해 주세요.",
}
_DEFAULT_HINT = "스레드를 읽지 못했습니다. 잠시 후 다시 시도해 주세요."


class ThreadReadError(Exception):
    """The thread could not be read; the message is safe to show in Slack."""


@dataclass(frozen=True)
class ThreadMessage:
    user: str
    ts: str
    text: str


class SlackThreadReader:
    def __init__(self, client: Any) -> None:  # a slack_sdk `WebClient` (or a test fake)
        self._client = client

    def read(self, channel_id: str, thread_ts: str) -> list[ThreadMessage]:
        messages: list[ThreadMessage] = []
        cursor: str | None = None
        while True:
            response = self._fetch(channel_id, thread_ts, cursor)
            messages.extend(_to_messages(response.get("messages", [])))
            cursor = (response.get("response_metadata") or {}).get("next_cursor") or None
            if cursor is None:
                return messages

    def _fetch(self, channel_id: str, thread_ts: str, cursor: str | None) -> Any:
        arguments: dict[str, Any] = {"channel": channel_id, "ts": thread_ts, "limit": _PAGE_LIMIT}
        if cursor:
            arguments["cursor"] = cursor
        try:
            return self._client.conversations_replies(**arguments)
        except SlackApiError as exc:
            code = str(exc.response.get("error", ""))
            raise ThreadReadError(_ERROR_HINTS.get(code, _DEFAULT_HINT)) from None


def _to_messages(raw_messages: list[dict[str, Any]]) -> list[ThreadMessage]:
    return [
        ThreadMessage(user=str(raw.get("user", "")), ts=str(raw["ts"]), text=str(raw["text"]))
        for raw in raw_messages
        if not raw.get("bot_id")
        and raw.get("subtype") not in _SYSTEM_SUBTYPES
        and str(raw.get("text", "")).strip()
    ]


@dataclass(frozen=True)
class Transcript:
    text: str
    message_count: int
    truncated: bool


def build_transcript(
    messages: list[ThreadMessage],
    *,
    tz: tzinfo | None = None,
    max_messages: int = MAX_MESSAGES,
    max_chars: int = MAX_CHARS,
) -> Transcript:
    """One `[HH:MM] author: text` line per message. Over a limit, the oldest messages
    go first: the end of a thread usually holds its outcome."""
    kept = messages[-max_messages:]
    lines = [_format_line(message, tz) for message in kept]
    while len(lines) > 1 and len("\n".join(lines)) > max_chars:
        lines.pop(0)
    text = "\n".join(lines)[-max_chars:]
    return Transcript(text=text, message_count=len(lines), truncated=len(lines) < len(messages))


def _format_line(message: ThreadMessage, tz: tzinfo | None) -> str:
    moment = datetime.fromtimestamp(float(message.ts), tz=tz)
    return f"[{moment:%H:%M}] {message.user}: {message.text}"

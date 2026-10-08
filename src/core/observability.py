"""Request-scoped fields injected into local process logs."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_request_id: ContextVar[str] = ContextVar("request_id", default="-")
_channel_id: ContextVar[str] = ContextVar("channel_id", default="-")
_thread_ts: ContextVar[str] = ContextVar("thread_ts", default="-")


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        record.channel_id = _channel_id.get()
        record.thread_ts = _thread_ts.get()
        return True


@contextmanager
def request_log_context(request_id: str, channel_id: str, thread_ts: str) -> Iterator[None]:
    request_token = _request_id.set(request_id)
    channel_token = _channel_id.set(channel_id)
    thread_token = _thread_ts.set(thread_ts)
    try:
        yield
    finally:
        _request_id.reset(request_token)
        _channel_id.reset(channel_token)
        _thread_ts.reset(thread_token)

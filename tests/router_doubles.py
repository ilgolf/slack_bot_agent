"""A stand-in for the LLM intent classifier in tests that drive the coordinator."""

from __future__ import annotations

from src.request_router import RequestIntent
from src.thread_context import ThreadWorkContext

_CODE_WORK_WORDS = ("수정", "진행", "plan", "이어서")


class CodeWorkWords:
    """Says `code_work` when the message holds one of a few work words, otherwise analysis."""

    def classify(self, text: str, thread_context: ThreadWorkContext | None) -> RequestIntent:
        if any(word in text.casefold() for word in _CODE_WORK_WORDS):
            return RequestIntent.CODE_WORK
        return RequestIntent.PROJECT_ANALYSIS

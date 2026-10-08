"""A router for tests that drive the coordinator with plain-language requests."""

from __future__ import annotations

from src.slack.request_router import RequestIntent, RequestRouter, RoutedRequest

_CODE_WORK_WORDS = ("수정", "진행", "plan", "이어서")


class WordGatedRouter(RequestRouter):
    """A plan-mode router that treats a message as code work only when it holds one of a few
    work words; everything else is analysis. Fixed commands keep their own intents."""

    def __init__(self) -> None:
        super().__init__(code_work_mode="plan")

    def route(self, text: str, *, project_name: str | None = None) -> RoutedRequest:
        routed = super().route(text, project_name=project_name)
        if routed.mode_decided and not any(word in text.casefold() for word in _CODE_WORK_WORDS):
            return RoutedRequest(RequestIntent.PROJECT_ANALYSIS, routed.text, project_name)
        return routed

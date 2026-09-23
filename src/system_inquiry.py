"""Deterministic answers for bot capabilities that must not trigger project analysis."""

from __future__ import annotations

import re

_SLACK_MENTION = re.compile(r"<@[^>]+>")
_MCP_MARKERS = ("mcp", "linear", "notion")
_CAPABILITY_MARKERS = ("연동", "가능", "설정", "확인", "지원")


def answer_system_inquiry(text: str) -> str | None:
    """Report unconfigured external integrations without inventing file evidence."""
    request = _SLACK_MENTION.sub("", text).casefold()
    if not any(marker in request for marker in _MCP_MARKERS):
        return None
    if not any(marker in request for marker in _CAPABILITY_MARKERS):
        return None
    return (
        "현재 Goodra-bot에는 Linear·Notion MCP 서버가 연결되어 있지 않습니다.\n"
        "MCP 기반 연동 구조로 확장하는 것은 가능하지만, 지금은 티켓 조회·생성·수정을 실행할 "
        "도구와 인증 설정이 없습니다. 연결 후에는 읽기 작업은 조회로, 생성·수정 작업은 "
        "미리보기와 `실행` 확인 뒤에 처리하도록 적용할 수 있습니다."
    )

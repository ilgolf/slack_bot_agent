"""Deterministic answers for bot capabilities that must not trigger project analysis."""

from __future__ import annotations

import re

from src.linear_tools import linear_capability

_SLACK_MENTION = re.compile(r"<@[^>]+>")
_LINEAR_MARKERS = ("linear", "graphql")
_LINEAR_CAPABILITY_MARKERS = (
    "연동",
    "가능",
    "설정",
    "확인",
    "지원",
    "mcp",
    "설계",
    "기반",
    "학습",
    "operation",
    "rest",
    "api",
    "인증",
    "키",
    "구현",
)
_NOTION_MCP_MARKERS = ("notion", "mcp")
_MCP_CAPABILITY_MARKERS = ("연동", "가능", "설정", "확인", "지원")


def answer_system_inquiry(
    text: str, *, linear_api_key_configured: bool | None = None
) -> str | None:
    """Answer fixed integration questions without network access or mutations."""
    request = _SLACK_MENTION.sub("", text).casefold()
    if any(marker in request for marker in _LINEAR_MARKERS) and any(
        marker in request for marker in _LINEAR_CAPABILITY_MARKERS
    ):
        return _linear_capability_response(linear_api_key_configured)
    if not all(marker in request for marker in _NOTION_MCP_MARKERS):
        return None
    if not any(marker in request for marker in _MCP_CAPABILITY_MARKERS):
        return None
    return (
        "현재 Goodra-bot에는 Notion MCP 서버가 연결되어 있지 않습니다."
    )


def _linear_capability_response(linear_api_key_configured: bool | None) -> str:
    capability = linear_capability()
    reads = ", ".join(operation.description for operation in capability.read_operations)
    mutations = ", ".join(operation.description for operation in capability.mutation_operations)
    key_status = _key_status(linear_api_key_configured)
    return (
        "현재 Linear 직접 연동 코드는 GraphQL 기반입니다.\n"
        f"- Endpoint: `{capability.endpoint}`\n"
        f"- 지원 조회: {reads}\n"
        f"- 지원 변경: {mutations}\n"
        "- 변경은 미리보기 뒤 같은 스레드에서 `실행`을 확인해야 합니다.\n"
        "- MCP 서버는 연결되어 있지 않습니다. 임의 REST 요청·임의 GraphQL operation·"
        "Linear schema 전체는 지원하지 않습니다.\n"
        f"- 인증: {capability.authentication}; {key_status}\n"
        f"공식 GraphQL 문서: {capability.documentation_url}"
    )


def _key_status(linear_api_key_configured: bool | None) -> str:
    if linear_api_key_configured is True:
        return "현재 API key가 설정되어 있어 workspace 요청을 시도할 수 있습니다"
    if linear_api_key_configured is False:
        return "현재 API key가 설정되지 않아 workspace 요청은 실행할 수 없습니다"
    return "현재 API key 설정 여부는 이 경로에서 확인하지 않았습니다"

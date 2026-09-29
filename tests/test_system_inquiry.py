"""Capability questions bypass project analysis and report actual bot support."""

from __future__ import annotations

from pathlib import Path

from src.agent import FakeAnalysisAgent
from src.dispatch import dispatch_command
from src.system_inquiry import answer_system_inquiry
from src.thread_context import ThreadContextStore


def test_linear_mcp_capability_question_is_not_sent_to_analysis_agent(tmp_path: Path) -> None:
    store = ThreadContextStore(root=tmp_path)

    response = dispatch_command(
        "C1",
        "1.1",
        "<@U1> Linear ticket MCP 연동 가능한지 확인해줄래?",
        thread_context=store,
        agent=FakeAnalysisAgent(),
    )

    assert "연결되어 있지 않습니다" in response
    assert "가짜 분석" not in response


def test_linear_graphql_design_question_reports_fixed_support_without_key() -> None:
    response = answer_system_inquiry(
        "GraphQL 기반으로 설계한 거야?", linear_api_key_configured=False
    )

    assert response is not None
    assert "https://api.linear.app/graphql" in response
    assert "list_teams" not in response
    assert "팀 목록 조회" in response
    assert "임의 REST 요청" in response
    assert "MCP 서버는 연결되어 있지 않습니다" in response
    assert "API key가 설정되지 않아" in response


def test_linear_capability_answer_never_calls_analysis_agent(tmp_path: Path) -> None:
    store = ThreadContextStore(root=tmp_path)

    response = dispatch_command(
        "C1",
        "1.1",
        "Linear가 지원하는 operation이 뭐야?",
        thread_context=store,
        agent=FakeAnalysisAgent(),
        linear_api_key_configured=True,
    )

    assert "이슈 생성" in response
    assert "workspace 요청을 시도할 수 있습니다" in response
    assert "가짜 분석" not in response


def test_unrelated_mcp_text_does_not_claim_an_integration_status() -> None:
    assert answer_system_inquiry("MCP 프로토콜이 뭐야?") is None

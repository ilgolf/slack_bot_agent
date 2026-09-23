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


def test_unrelated_mcp_text_does_not_claim_an_integration_status() -> None:
    assert answer_system_inquiry("MCP 프로토콜이 뭐야?") is None

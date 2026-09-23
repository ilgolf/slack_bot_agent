"""Code analysis agent boundary.

`FakeAnalysisAgent` stands in for the real LLM-backed agent. `analyze` takes only a
question — no `project_path` — matching `LangChainAnalysisAgent`'s contract now that
project selection happens through tool-calling (see plan.md Section 8).
"""

from __future__ import annotations

from src.agent import AnalysisResult, FakeAnalysisAgent


def test_fake_agent_returns_canned_analysis_result() -> None:
    agent = FakeAnalysisAgent()

    result = agent.analyze("이 프로젝트는 뭐 하는 거야?")

    assert isinstance(result, AnalysisResult)
    assert result.summary
    assert result.findings

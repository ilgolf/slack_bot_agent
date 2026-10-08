"""Models served through the OpenAI Responses API return `content` as a list of
blocks; every JSON-reading call site must read the text out of those blocks."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import AIMessage, BaseMessage

from src.code.langchain_agent import LangChainAnalysisAgent, _parse_analysis_result
from src.code.planner import LangChainNextActionPlanner
from src.code.workflow import ExecutionPlan, ExecutionRisk, ExecutionStep
from src.core.project_resolver import ProjectResolver


def _blocks(text: str) -> list[dict[str, object]]:
    return [
        {"type": "reasoning", "summary": []},
        {"type": "text", "text": text, "annotations": []},
    ]


class _BlockListModel:
    def __init__(self, text: str) -> None:
        self.text = text

    def invoke(self, input: list[BaseMessage]) -> AIMessage:
        return AIMessage(content=_blocks(self.text))  # type: ignore[arg-type]


def test_analysis_result_parses_from_block_list() -> None:
    payload = json.dumps({"summary": "요약", "findings": ["a"], "limitations": []})

    result = _parse_analysis_result(_blocks(payload), sources=["plan.md"])

    assert result.summary == "요약"


def test_repair_steps_parse_from_block_list(tmp_path: Path) -> None:
    payload = json.dumps(
        {"steps": [{"action": "write_file", "path": "a.py", "content": "x = 1\n"}]}
    )
    agent = LangChainAnalysisAgent(
        chat_model=_BlockListModel(payload), project_resolver=ProjectResolver(root=tmp_path)
    )
    plan = ExecutionPlan(
        goal="g",
        project_name="p",
        affected_files=["a.py"],
        steps=[ExecutionStep(action="write_file", path="a.py", content="")],
        verification_commands=[],
        risk=ExecutionRisk.MODIFY,
    )

    steps = agent.create_repair_steps(plan, [], [])

    assert [step.path for step in steps] == ["a.py"]


def test_planner_final_answer_parses_from_block_list() -> None:
    payload = json.dumps({"summary": "요약", "findings": ["a"], "limitations": []})

    final = LangChainNextActionPlanner._parse_final(_blocks(payload))

    assert final.summary == "요약"

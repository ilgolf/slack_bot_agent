"""LangChainAnalysisAgent: `analyze(question)` — no `project_path` — backed by a
LangChain chat model with tool-calling access to a `ProjectResolver` (see plan.md
Sections 5-6, 8). Project selection happens through tool-calling, not a
pre-resolved path.

No test calls a real LLM — `FakeListChatModel` and hand-rolled fakes stand in.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from src.agent import AnalysisAgentError, AnalysisResult
from src.execution_workflow import ProjectContextLoader
from src.langchain_agent import LangChainAnalysisAgent
from src.project_resolver import ProjectResolver
from src.tools import list_files, read_file


def test_langchain_agent_analyze_returns_analysis_result(tmp_path: Path) -> None:
    project_resolver = ProjectResolver(root=tmp_path)
    chat_model = FakeListChatModel(responses=['{"summary": "fake summary", "findings": []}'])
    agent = LangChainAnalysisAgent(chat_model=chat_model, project_resolver=project_resolver)

    result = agent.analyze("이 프로젝트는 뭐 하는 거야?")

    assert isinstance(result, AnalysisResult)


class RecordingChatModel:
    def __init__(self, response_content: str) -> None:
        self.response_content = response_content
        self.last_input: list[BaseMessage] | None = None

    def invoke(self, input: list[BaseMessage]) -> AIMessage:
        self.last_input = input
        return AIMessage(content=self.response_content)


def test_langchain_agent_sends_question_and_parses_reply(tmp_path: Path) -> None:
    (tmp_path / "my-project").mkdir()
    project_resolver = ProjectResolver(root=tmp_path)
    chat_model = RecordingChatModel('{"summary": "요약", "findings": ["finding1"]}')
    agent = LangChainAnalysisAgent(chat_model=chat_model, project_resolver=project_resolver)
    question = "my-project는 뭐 하는 거야?"

    result = agent.analyze(question)

    assert chat_model.last_input is not None
    prompt = chat_model.last_input[0].content
    assert question in prompt
    assert result == AnalysisResult(summary="요약", findings=["finding1"])


def test_langchain_agent_parses_fenced_json_reply(tmp_path: Path) -> None:
    (tmp_path / "my-project").mkdir()
    project_resolver = ProjectResolver(root=tmp_path)
    chat_model = RecordingChatModel(
        '```json\n{\n\u00a0 "summary": "요약",\n\u00a0 "findings": ["finding1"]\n}\n```'
    )
    agent = LangChainAnalysisAgent(chat_model=chat_model, project_resolver=project_resolver)

    result = agent.analyze("my-project 분석해줘")

    assert result == AnalysisResult(summary="요약", findings=["finding1"])


def test_langchain_agent_summarizes_only_the_named_readme(tmp_path: Path) -> None:
    project_path = tmp_path / "my-project"
    project_path.mkdir()
    (project_path / "README.md").write_text("프로젝트 소개")
    chat_model = RecordingChatModel('{"summary": "요약", "findings": ["소개"]}')
    agent = LangChainAnalysisAgent(
        chat_model=chat_model,
        project_resolver=ProjectResolver(root=tmp_path),
    )

    result = agent.analyze("my-project README.md 요약해줘")

    assert result == AnalysisResult(summary="요약", findings=["소개"], sources=["README.md"])
    assert chat_model.last_input is not None
    assert "프로젝트 소개" in chat_model.last_input[0].content
    assert "다른 도구를 호출하거나 추측하지 마세요" in chat_model.last_input[0].content


def test_langchain_agent_requests_project_for_ambiguous_readme(tmp_path: Path) -> None:
    chat_model = RecordingChatModel('{"summary": "unused", "findings": []}')
    agent = LangChainAnalysisAgent(
        chat_model=chat_model,
        project_resolver=ProjectResolver(root=tmp_path),
    )

    result = agent.analyze("README.md 요약해줘")

    assert "프로젝트명" in result.summary
    assert chat_model.last_input is None


def test_langchain_agent_requests_project_before_general_analysis(tmp_path: Path) -> None:
    chat_model = RecordingChatModel('{"summary": "unused", "findings": []}')
    agent = LangChainAnalysisAgent(
        chat_model=chat_model,
        project_resolver=ProjectResolver(root=tmp_path),
    )

    result = agent.analyze("인증 흐름을 분석해줘")

    assert "프로젝트명" in result.summary
    assert chat_model.last_input is None


def test_langchain_agent_leaves_email_csv_creation_to_the_artifact_workflow(
    tmp_path: Path,
) -> None:
    chat_model = RecordingChatModel('{"summary": "unused", "findings": []}')
    agent = LangChainAnalysisAgent(
        chat_model=chat_model,
        project_resolver=ProjectResolver(root=tmp_path),
    )

    result = agent.analyze("~/orca root에 관광 효성중공업 이메일들 csv로 만들어줘")

    assert "프로젝트명" in result.summary
    assert chat_model.last_input is None


def test_langchain_agent_creates_a_structured_execution_plan(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "README.md").write_text("before")
    chat_model = RecordingChatModel(
        '{"goal": "README 갱신", "project_name": "my-project", '
        '"affected_files": ["README.md"], "steps": [{"action": "write_file", '
        '"path": "README.md", "content": "after"}], '
        '"verification_commands": ["run_tests"], "risk": "modify"}'
    )
    resolver = ProjectResolver(root=tmp_path)
    agent = LangChainAnalysisAgent(chat_model=chat_model, project_resolver=resolver)

    plan = agent.create_execution_plan(
        "my-project README.md 수정해줘",
        ProjectContextLoader(resolver).load("my-project", target_paths=["README.md"]),
        [],
    )

    assert plan.project_name == "my-project"
    assert plan.steps[0].path == "README.md"
    assert chat_model.last_input is not None
    assert "삭제·네트워크·의존성 변경" in chat_model.last_input[0].content


def test_langchain_agent_summarizes_only_the_named_file(tmp_path: Path) -> None:
    project_path = tmp_path / "my-project"
    (project_path / "src").mkdir(parents=True)
    (project_path / "src" / "service.py").write_text("def run(): pass")
    chat_model = RecordingChatModel('{"summary": "요약", "findings": []}')
    agent = LangChainAnalysisAgent(
        chat_model=chat_model,
        project_resolver=ProjectResolver(root=tmp_path),
    )

    result = agent.analyze("my-project src/service.py 요약해줘")

    assert result.sources == ["src/service.py"]
    assert chat_model.last_input is not None
    assert "def run(): pass" in chat_model.last_input[0].content


def test_langchain_agent_returns_policy_error_to_model_for_another_project(tmp_path: Path) -> None:
    (tmp_path / "my-project").mkdir()
    (tmp_path / "other-project").mkdir()
    tool_call_response = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "list_files",
                "args": {"project_name": "other-project"},
                "id": "call_1",
            }
        ],
    )
    final_response = AIMessage(content='{"summary": "요약", "findings": []}')
    agent = LangChainAnalysisAgent(
        chat_model=SequencedChatModel([tool_call_response, final_response, final_response]),
        project_resolver=ProjectResolver(root=tmp_path),
        tools=[list_files],
    )

    result = agent.analyze("my-project 분석해줘")

    assert "근거 파일" in result.summary


def test_langchain_agent_rejects_multiple_tool_calls_in_one_step(tmp_path: Path) -> None:
    (tmp_path / "my-project").mkdir()
    tool_call_response = AIMessage(
        content="",
        tool_calls=[
            {"name": "list_files", "args": {"project_name": "my-project"}, "id": "call_1"},
            {"name": "list_files", "args": {"project_name": "my-project"}, "id": "call_2"},
        ],
    )
    agent = LangChainAnalysisAgent(
        chat_model=SequencedChatModel([tool_call_response]),
        project_resolver=ProjectResolver(root=tmp_path),
        tools=[list_files],
    )

    with pytest.raises(AnalysisAgentError, match="exactly one"):
        agent.analyze("my-project 분석해줘")


def test_langchain_agent_replans_before_returning_an_unsourced_final_answer(tmp_path: Path) -> None:
    project_path = tmp_path / "my-project"
    project_path.mkdir()
    (project_path / "README.md").write_text("근거")
    premature_final = AIMessage(content='{"summary": "근거 없음", "findings": []}')
    read_file_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "read_file",
                "args": {"project_name": "my-project", "relative_path": "README.md"},
                "id": "call_1",
            }
        ],
    )
    sourced_final = AIMessage(content='{"summary": "근거 있음", "findings": []}')
    chat_model = SequencedChatModel([premature_final, read_file_call, sourced_final])
    agent = LangChainAnalysisAgent(
        chat_model=chat_model,
        project_resolver=ProjectResolver(root=tmp_path),
        tools=[read_file],
    )

    result = agent.analyze("my-project 분석해줘")

    assert result.summary == "근거 있음"
    assert result.sources == ["README.md"]
    assert len(chat_model.invocations) == 3


class SequencedChatModel:
    def __init__(self, responses: list[AIMessage]) -> None:
        self._responses = list(responses)
        self.invocations: list[list[BaseMessage]] = []

    def invoke(self, input: list[BaseMessage]) -> AIMessage:
        self.invocations.append(input)
        return self._responses.pop(0)


def test_langchain_agent_drives_tool_calling_loop(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="src.langchain_agent")
    project_path = tmp_path / "my-project"
    project_path.mkdir()
    (project_path / "a.txt").write_text("a")
    project_resolver = ProjectResolver(root=tmp_path)

    tool_call_response = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "read_file",
                "args": {"project_name": "my-project", "relative_path": "a.txt"},
                "id": "call_1",
            }
        ],
    )
    final_response = AIMessage(content='{"summary": "요약", "findings": ["a.txt exists"]}')
    chat_model = SequencedChatModel([tool_call_response, final_response])
    agent = LangChainAnalysisAgent(
        chat_model=chat_model, project_resolver=project_resolver, tools=[list_files, read_file]
    )

    result = agent.analyze("my-project에 무슨 파일이 있어?")

    assert result == AnalysisResult(
        summary="요약",
        findings=["a.txt exists"],
        sources=["a.txt"],
    )
    assert len(chat_model.invocations) == 2

    second_call_messages = chat_model.invocations[1]
    tool_messages = [m for m in second_call_messages if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert "a" in tool_messages[0].content
    assert "tool_call_started name=read_file" in caplog.text
    assert "tool_call_completed name=read_file result_type=str" in caplog.text


def test_langchain_agent_raises_analysis_agent_error_on_invalid_json(tmp_path: Path) -> None:
    (tmp_path / "my-project").mkdir()
    project_resolver = ProjectResolver(root=tmp_path)
    chat_model = FakeListChatModel(responses=["이건 JSON이 아니에요"])
    agent = LangChainAnalysisAgent(chat_model=chat_model, project_resolver=project_resolver)

    with pytest.raises(AnalysisAgentError):
        agent.analyze("my-project 분석해줘")


def test_langchain_agent_raises_analysis_agent_error_on_missing_fields(tmp_path: Path) -> None:
    (tmp_path / "my-project").mkdir()
    project_resolver = ProjectResolver(root=tmp_path)
    chat_model = FakeListChatModel(responses=['{"foo": "bar"}'])
    agent = LangChainAnalysisAgent(chat_model=chat_model, project_resolver=project_resolver)

    with pytest.raises(AnalysisAgentError):
        agent.analyze("my-project 분석해줘")


class AlwaysToolCallChatModel:
    def invoke(self, input: list[BaseMessage]) -> AIMessage:
        return AIMessage(
            content="",
            tool_calls=[
                {"name": "list_files", "args": {"project_name": "my-project"}, "id": "call"}
            ],
        )


def test_langchain_agent_raises_analysis_agent_error_when_tool_loop_exceeds_max_iterations(
    tmp_path: Path,
) -> None:
    project_path = tmp_path / "my-project"
    project_path.mkdir()
    project_resolver = ProjectResolver(root=tmp_path)
    chat_model = AlwaysToolCallChatModel()
    agent = LangChainAnalysisAgent(
        chat_model=chat_model, project_resolver=project_resolver, tools=[list_files]
    )

    with pytest.raises(AnalysisAgentError):
        agent.analyze("my-project 분석해줘")

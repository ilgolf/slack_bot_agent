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
from langchain_core.tools import StructuredTool

from src.agent import AnalysisResult
from src.code_agent_loop import AgentPlan, ToolCall
from src.code_agent_planner import LangChainNextActionPlanner
from src.execution_workflow import ExistingFile, ProjectContextLoader
from src.langchain_agent import LangChainAnalysisAgent
from src.project_resolver import ProjectResolver
from src.tools import list_files, read_file


def test_langchain_agent_analyze_returns_analysis_result(tmp_path: Path) -> None:
    project_resolver = ProjectResolver(root=tmp_path)
    chat_model = FakeListChatModel(responses=['{"summary": "fake summary", "findings": []}'])
    agent = LangChainAnalysisAgent(chat_model=chat_model, project_resolver=project_resolver)

    result = agent.analyze("이 프로젝트는 뭐 하는 거야?")

    assert isinstance(result, AnalysisResult)


class FakeBindableModel:
    """A minimal chat model stand-in that supports `bind_tools` (unlike
    `FakeListChatModel`) and always answers with one tool call."""

    def __init__(self) -> None:
        self.bound_tools: list[StructuredTool] | None = None

    def bind_tools(self, tools: list[StructuredTool]) -> FakeBindableModel:
        self.bound_tools = tools
        return self

    def invoke(self, input: list[BaseMessage]) -> AIMessage:
        del input
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"relative_path": "README.md"},
                    "id": "call_1",
                }
            ],
        )


def test_build_planner_binds_project_bound_tools_without_a_project_name_field(
    tmp_path: Path,
) -> None:
    """The project is already selected before planning starts, so the schema
    exposed to the model must not even have a `project_name` field — the
    model literally cannot ask to analyze a different project."""
    (tmp_path / "my-project").mkdir()
    chat_model = FakeBindableModel()
    agent = LangChainAnalysisAgent(
        chat_model=chat_model,
        project_resolver=ProjectResolver(root=tmp_path),
        tools=[read_file],
    )

    planner = agent._build_planner("my-project")

    assert isinstance(planner, LangChainNextActionPlanner)
    assert chat_model.bound_tools is not None
    read_file_tool = next(tool for tool in chat_model.bound_tools if tool.name == "read_file")
    assert set(read_file_tool.args) == {"relative_path"}
    action = planner.next_action(
        AgentPlan(goal="분석", selected_project="my-project", steps=[]), []
    )
    assert action == ToolCall(name="read_file", args={"relative_path": "README.md"})


def test_analyze_accepts_channel_and_thread_kwargs_for_backward_compatibility(
    tmp_path: Path,
) -> None:
    """A future thread-scoped trace store needs the caller's thread identity,
    but every existing call site must keep working unchanged — hence the
    backward-compatible defaults."""
    project_resolver = ProjectResolver(root=tmp_path)
    chat_model = FakeListChatModel(responses=['{"summary": "fake summary", "findings": []}'])
    agent = LangChainAnalysisAgent(chat_model=chat_model, project_resolver=project_resolver)

    result = agent.analyze("이 프로젝트는 뭐 하는 거야?", channel_id="C1", thread_ts="T1")

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


def test_langchain_agent_records_trace_in_thread_trace_store(tmp_path: Path) -> None:
    project_path = tmp_path / "my-project"
    project_path.mkdir()
    (project_path / "README.md").write_text("intro")
    chat_model = RecordingChatModel('{"summary": "요약", "findings": []}')
    agent = LangChainAnalysisAgent(
        chat_model=chat_model,
        project_resolver=ProjectResolver(root=tmp_path),
    )

    agent.analyze("my-project README.md 요약해줘", channel_id="C1", thread_ts="T1")

    trace = agent.thread_trace_store.get("C1", "T1")
    assert trace is not None
    assert agent.thread_trace_store.get("C2", "T2") is None


def test_langchain_agent_generates_unique_request_id_per_analyze_call(tmp_path: Path) -> None:
    """A single agent instance is shared across Slack threads/requests, so a
    fixed `request_id` would make every request's trace log lines
    indistinguishable from every other's."""
    project_path = tmp_path / "my-project"
    project_path.mkdir()
    (project_path / "README.md").write_text("intro")
    chat_model = RecordingChatModel('{"summary": "요약", "findings": []}')
    agent = LangChainAnalysisAgent(
        chat_model=chat_model,
        project_resolver=ProjectResolver(root=tmp_path),
    )

    agent.analyze("my-project README.md 요약해줘")
    first_trace = agent.thread_trace_store.get("-", "-")
    assert first_trace is not None
    first_request_id = first_trace.request_id

    agent.analyze("my-project README.md 요약해줘")
    second_trace = agent.thread_trace_store.get("-", "-")
    assert second_trace is not None
    second_request_id = second_trace.request_id

    assert first_request_id != second_request_id


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
        [],
    )

    assert plan.project_name == "my-project"
    assert plan.steps[0].path == "README.md"
    assert chat_model.last_input is not None
    assert "삭제·네트워크·의존성 변경" in chat_model.last_input[0].content


def test_langchain_agent_execution_plan_prompt_includes_existing_file_content(
    tmp_path: Path,
) -> None:
    """The planner must see what's already there, not just AGENTS.md/skills —
    otherwise it edits blind and can silently discard existing code."""
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "README.md").write_text("very unique existing content 12345")
    chat_model = RecordingChatModel(
        '{"goal": "README 갱신", "project_name": "my-project", '
        '"affected_files": ["README.md"], "steps": [{"action": "write_file", '
        '"path": "README.md", "content": "after"}], '
        '"verification_commands": [], "risk": "modify"}'
    )
    resolver = ProjectResolver(root=tmp_path)
    agent = LangChainAnalysisAgent(chat_model=chat_model, project_resolver=resolver)

    agent.create_execution_plan(
        "my-project README.md 수정해줘",
        ProjectContextLoader(resolver).load("my-project", target_paths=["README.md"]),
        [],
        [
            ExistingFile(relative_path="README.md", content="very unique existing content 12345"),
            ExistingFile(relative_path="new_feature.py", content=None),
        ],
    )

    assert chat_model.last_input is not None
    prompt = chat_model.last_input[0].content
    assert "very unique existing content 12345" in prompt
    assert "new_feature.py" in prompt
    assert "새 파일, 아직 존재하지 않음" in prompt


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


def test_langchain_agent_trace_uses_registry_category_not_a_hardcoded_literal(
    tmp_path: Path,
) -> None:
    """A tool name the policy allows by literal match but that isn't actually
    registered (missing from `tools=`) must not be traced as `project_read` —
    that would misreport a call the registry never ran."""
    project_path = tmp_path / "my-project"
    project_path.mkdir()
    list_call = AIMessage(
        content="",
        tool_calls=[
            {"name": "list_files", "args": {"project_name": "my-project"}, "id": "call_1"}
        ],
    )
    unregistered_read_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "read_file",
                "args": {"project_name": "my-project", "relative_path": "x.txt"},
                "id": "call_2",
            }
        ],
    )
    final_response = AIMessage(content='{"summary": "요약", "findings": []}')
    agent = LangChainAnalysisAgent(
        chat_model=SequencedChatModel(
            [list_call, unregistered_read_call, final_response, final_response]
        ),
        project_resolver=ProjectResolver(root=tmp_path),
        tools=[list_files],  # read_file is deliberately omitted
    )

    agent.analyze("my-project 분석해줘")

    trace = agent.thread_trace_store.get("-", "-")
    assert trace is not None
    read_file_steps = [step for step in trace.steps if step.tool_name == "read_file"]
    assert read_file_steps
    assert read_file_steps[0].category == "unknown"


def test_langchain_agent_treats_blocked_outcome_as_failure_not_a_source(tmp_path: Path) -> None:
    """A registry-level failure (here: unregistered tool) must not be silently
    counted as a read source, and `CodeAgentLoop` traces it under its own
    real status (`blocked`) rather than a generic `failed`."""
    project_path = tmp_path / "my-project"
    project_path.mkdir()
    list_call = AIMessage(
        content="",
        tool_calls=[{"name": "list_files", "args": {}, "id": "call_1"}],
    )
    unregistered_read_call = AIMessage(
        content="",
        tool_calls=[{"name": "read_file", "args": {"relative_path": "x.txt"}, "id": "call_2"}],
    )
    premature_final = AIMessage(content='{"summary": "요약", "findings": []}')
    agent = LangChainAnalysisAgent(
        chat_model=SequencedChatModel(
            [list_call, unregistered_read_call, premature_final, premature_final]
        ),
        project_resolver=ProjectResolver(root=tmp_path),
        tools=[list_files],  # read_file is deliberately omitted
    )

    result = agent.analyze("my-project 분석해줘")

    assert result.sources == []
    trace = agent.thread_trace_store.get("-", "-")
    assert trace is not None
    read_file_steps = [step for step in trace.steps if step.tool_name == "read_file"]
    assert read_file_steps
    assert read_file_steps[0].outcome == "blocked"


def test_langchain_agent_replans_multiple_tool_calls_in_one_step(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "a.txt").write_text("a")
    multiple_call_response = AIMessage(
        content="",
        tool_calls=[
            {"name": "list_files", "args": {}, "id": "call_1"},
            {"name": "list_files", "args": {}, "id": "call_2"},
        ],
    )
    list_call = AIMessage(
        content="",
        tool_calls=[{"name": "list_files", "args": {}, "id": "call_3"}],
    )
    read_call = AIMessage(
        content="",
        tool_calls=[{"name": "read_file", "args": {"relative_path": "a.txt"}, "id": "call_4"}],
    )
    agent = LangChainAnalysisAgent(
        chat_model=SequencedChatModel(
            [
                multiple_call_response,
                list_call,
                read_call,
                AIMessage(content='{"summary":"ok","findings":[]}'),
            ]
        ),
        project_resolver=ProjectResolver(root=tmp_path),
        tools=[list_files, read_file],
    )

    result = agent.analyze("my-project 분석해줘")

    assert result.summary == "ok"
    assert result.sources == ["a.txt"]


def test_langchain_agent_reports_insufficient_evidence_without_retrying_the_model(
    tmp_path: Path,
) -> None:
    """`CodeAgentLoop`'s insufficient-evidence gate is terminal, not a
    nudge-and-retry — an acceptable simplification the global guarantee
    explicitly allows (either replan *or* report a limitation)."""
    project_path = tmp_path / "my-project"
    project_path.mkdir()
    (project_path / "README.md").write_text("근거")
    premature_final = AIMessage(content='{"summary": "근거 없음", "findings": []}')
    chat_model = SequencedChatModel([premature_final])
    agent = LangChainAnalysisAgent(
        chat_model=chat_model,
        project_resolver=ProjectResolver(root=tmp_path),
        tools=[read_file],
    )

    result = agent.analyze("my-project 분석해줘")

    assert result.sources == []
    assert "근거 파일" in result.summary
    assert len(chat_model.invocations) == 1


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
    caplog.set_level(logging.INFO, logger="src.agent_trace")
    project_path = tmp_path / "my-project"
    project_path.mkdir()
    (project_path / "a.txt").write_text("a")
    project_resolver = ProjectResolver(root=tmp_path)

    list_call_response = AIMessage(
        content="",
        tool_calls=[{"name": "list_files", "args": {"relative_path": "."}, "id": "call_1"}],
    )
    read_call_response = AIMessage(
        content="",
        tool_calls=[{"name": "read_file", "args": {"relative_path": "a.txt"}, "id": "call_2"}],
    )
    final_response = AIMessage(content='{"summary": "요약", "findings": ["a.txt exists"]}')
    chat_model = SequencedChatModel([list_call_response, read_call_response, final_response])
    agent = LangChainAnalysisAgent(
        chat_model=chat_model, project_resolver=project_resolver, tools=[list_files, read_file]
    )

    result = agent.analyze("my-project에 무슨 파일이 있어?")

    assert result == AnalysisResult(
        summary="요약",
        findings=["a.txt exists"],
        sources=["a.txt"],
    )
    assert len(chat_model.invocations) == 3

    third_call_messages = chat_model.invocations[2]
    tool_messages = [m for m in third_call_messages if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 2
    assert "a.txt" in tool_messages[0].content
    assert "a" in tool_messages[1].content
    # Every tool call is now logged through the content-free AgentTraceRecorder,
    # not a hand-rolled `logger.info` call in this module.
    assert "tool=read_file" in caplog.text
    assert "outcome=ok" in caplog.text


def test_langchain_agent_reports_limitation_on_invalid_json(tmp_path: Path) -> None:
    """An unparsable model reply must not raise and abort the whole Slack
    request — it must come back as a normal, explainable `AnalysisResult`."""
    (tmp_path / "my-project").mkdir()
    project_resolver = ProjectResolver(root=tmp_path)
    chat_model = FakeListChatModel(responses=["이건 JSON이 아니에요"])
    agent = LangChainAnalysisAgent(chat_model=chat_model, project_resolver=project_resolver)

    result = agent.analyze("my-project 분석해줘")

    assert result.limitations


def test_langchain_agent_reports_limitation_on_missing_fields(tmp_path: Path) -> None:
    (tmp_path / "my-project").mkdir()
    project_resolver = ProjectResolver(root=tmp_path)
    chat_model = FakeListChatModel(responses=['{"foo": "bar"}'])
    agent = LangChainAnalysisAgent(chat_model=chat_model, project_resolver=project_resolver)

    result = agent.analyze("my-project 분석해줘")

    assert result.limitations


class AlwaysToolCallChatModel:
    def invoke(self, input: list[BaseMessage]) -> AIMessage:
        return AIMessage(
            content="",
            tool_calls=[{"name": "list_files", "args": {}, "id": "call"}],
        )


def test_langchain_agent_reports_limitation_when_model_repeats_the_same_call(
    tmp_path: Path,
) -> None:
    """`CodeAgentLoop` blocks a duplicate call after one warning, well before
    `max_tool_iterations` — the old iteration-budget error no longer applies,
    but the request must still end in a clear, non-raising limitation."""
    project_path = tmp_path / "my-project"
    project_path.mkdir()
    project_resolver = ProjectResolver(root=tmp_path)
    chat_model = AlwaysToolCallChatModel()
    agent = LangChainAnalysisAgent(
        chat_model=chat_model, project_resolver=project_resolver, tools=[list_files]
    )

    result = agent.analyze("my-project 분석해줘")

    assert result.limitations
    assert "실행한 도구" in result.limitations[0]


class UniqueToolCallChatModel:
    """Never finalizes — always issues a fresh, non-duplicate `read_file`
    call, so the loop can only stop by exhausting its step budget."""

    def __init__(self) -> None:
        self.counter = 0

    def invoke(self, input: list[BaseMessage]) -> AIMessage:
        del input
        self.counter += 1
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"relative_path": f"f{self.counter}.txt"},
                    "id": f"call_{self.counter}",
                }
            ],
        )


def test_langchain_agent_reports_limitation_when_tool_budget_is_exhausted(
    tmp_path: Path,
) -> None:
    project_path = tmp_path / "my-project"
    project_path.mkdir()
    (project_path / "f1.txt").write_text("1")
    (project_path / "f2.txt").write_text("2")
    project_resolver = ProjectResolver(root=tmp_path)
    chat_model = UniqueToolCallChatModel()
    agent = LangChainAnalysisAgent(
        chat_model=chat_model,
        project_resolver=project_resolver,
        tools=[read_file],
        max_tool_iterations=2,
    )

    result = agent.analyze("my-project 분석해줘")

    assert result.limitations
    assert "실행한 도구" in result.limitations[0]
    assert result.sources == ["f1.txt", "f2.txt"]

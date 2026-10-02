"""CodeAgentAnalysisAgent: delegates read-only analysis to an injected `AgentRunner`
(plan.md Phase 12). No test starts a real SDK or CLI — a fake runner stands in.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from src.agent import (
    AnalysisAgentError,
    AnalysisResult,
    PlanResponseFormatError,
    insufficient_evidence_result,
)
from src.agent_trace import ThreadTraceStore
from src.code_agent_analysis import CodeAgentAnalysisAgent, RunnerError, RunnerResult, RunnerTimeout
from src.code_plan_prompts import build_code_plan_prompt
from src.execution_workflow import (
    CommandResult,
    ExecutionPlan,
    ExecutionRisk,
    ExistingFile,
    GitState,
    ProjectContext,
)
from src.project_resolver import ProjectResolver


@dataclass
class FakeRunner:
    name: str = "fake_runner"
    calls: list[str] = field(default_factory=list)
    cwds: list[Path] = field(default_factory=list)
    limits: list[tuple[float, int]] = field(default_factory=list)
    output: str = "ok"
    files_read: tuple[Path, ...] = ()
    error: Exception | None = None
    complete_calls: list[str] = field(default_factory=list)
    complete_output: str = ""
    complete_error: Exception | None = None

    def complete(self, prompt: str, *, timeout_seconds: float) -> str:
        self.complete_calls.append(prompt)
        if self.complete_error is not None:
            raise self.complete_error
        return self.complete_output

    def run(
        self, prompt: str, *, cwd: Path, timeout_seconds: float, max_turns: int
    ) -> RunnerResult:
        self.calls.append(prompt)
        self.cwds.append(cwd)
        self.limits.append((timeout_seconds, max_turns))
        if self.error is not None:
            raise self.error
        return RunnerResult(text=self.output, files_read=self.files_read)


def test_analyze_asks_for_project_name_without_calling_runner_when_project_unidentified(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    agent = CodeAgentAnalysisAgent(runner=runner, project_resolver=ProjectResolver(root=tmp_path))

    result = agent.analyze("이 프로젝트는 뭐 하는 거야?")

    assert result.summary == "분석할 프로젝트명을 알려주세요."
    assert runner.calls == []


def test_analyze_runs_runner_once_in_the_project_directory(tmp_path: Path) -> None:
    project_dir = tmp_path / "logifine-api-tbd"
    project_dir.mkdir()
    runner = FakeRunner(files_read=(project_dir / "Order.java",))
    agent = CodeAgentAnalysisAgent(runner=runner, project_resolver=ProjectResolver(root=tmp_path))

    agent.analyze("logifine-api-tbd core 모듈 분석해")

    assert runner.cwds == [project_dir]


def test_analyze_passes_the_user_question_in_the_runner_prompt(tmp_path: Path) -> None:
    project_dir = tmp_path / "logifine-api-tbd"
    project_dir.mkdir()
    runner = FakeRunner(files_read=(project_dir / "Order.java",))
    agent = CodeAgentAnalysisAgent(runner=runner, project_resolver=ProjectResolver(root=tmp_path))

    agent.analyze("logifine-api-tbd core 모듈 분석해")

    assert len(runner.calls) == 1
    assert "logifine-api-tbd core 모듈 분석해" in runner.calls[0]


def test_analyze_passes_timeout_and_max_turns_to_the_runner(tmp_path: Path) -> None:
    project_dir = tmp_path / "logifine-api-tbd"
    project_dir.mkdir()
    runner = FakeRunner(files_read=(project_dir / "Order.java",))
    agent = CodeAgentAnalysisAgent(
        runner=runner,
        project_resolver=ProjectResolver(root=tmp_path),
        timeout_seconds=30.0,
        max_turns=5,
    )

    agent.analyze("logifine-api-tbd core 모듈 분석해")

    assert runner.limits == [(30.0, 5)]


def test_analyze_uses_the_runner_text_as_the_summary(tmp_path: Path) -> None:
    project_dir = tmp_path / "logifine-api-tbd"
    project_dir.mkdir()
    runner = FakeRunner(
        files_read=(project_dir / "Order.java",),
        output="\n## core 모듈\n도메인 모듈입니다.\n",
    )
    agent = CodeAgentAnalysisAgent(runner=runner, project_resolver=ProjectResolver(root=tmp_path))

    result = agent.analyze("logifine-api-tbd core 모듈 분석해")

    assert result == AnalysisResult(
        summary="## core 모듈\n도메인 모듈입니다.", findings=[], sources=["Order.java"]
    )


def test_analyze_raises_analysis_agent_error_when_the_runner_text_is_empty(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "logifine-api-tbd"
    project_dir.mkdir()
    runner = FakeRunner(files_read=(project_dir / "Order.java",), output="  \n")
    agent = CodeAgentAnalysisAgent(runner=runner, project_resolver=ProjectResolver(root=tmp_path))

    with pytest.raises(AnalysisAgentError):
        agent.analyze("logifine-api-tbd core 모듈 분석해")


def test_analyze_reports_files_read_as_project_relative_sources(tmp_path: Path) -> None:
    project_dir = tmp_path / "logifine-api-tbd"
    project_dir.mkdir()
    runner = FakeRunner(files_read=(project_dir / "core" / "src" / "Order.java",))
    agent = CodeAgentAnalysisAgent(runner=runner, project_resolver=ProjectResolver(root=tmp_path))

    result = agent.analyze("logifine-api-tbd core 모듈 분석해")

    assert result.sources == ["core/src/Order.java"]


def test_analyze_excludes_files_read_outside_the_project_from_sources(tmp_path: Path) -> None:
    project_dir = tmp_path / "logifine-api-tbd"
    project_dir.mkdir()
    runner = FakeRunner(
        files_read=(project_dir / "core" / "Order.java", tmp_path / "other" / "Secret.java")
    )
    agent = CodeAgentAnalysisAgent(runner=runner, project_resolver=ProjectResolver(root=tmp_path))

    result = agent.analyze("logifine-api-tbd core 모듈 분석해")

    assert result.sources == ["core/Order.java"]


def test_analyze_retries_the_runner_once_when_no_files_were_read(tmp_path: Path) -> None:
    (tmp_path / "logifine-api-tbd").mkdir()
    runner = FakeRunner()
    agent = CodeAgentAnalysisAgent(runner=runner, project_resolver=ProjectResolver(root=tmp_path))

    agent.analyze("logifine-api-tbd core 모듈 분석해")

    assert len(runner.calls) == 2
    assert "logifine-api-tbd core 모듈 분석해" in runner.calls[1]
    assert "소스 파일" in runner.calls[1]


def test_analyze_returns_insufficient_evidence_when_retry_also_reads_no_files(
    tmp_path: Path,
) -> None:
    (tmp_path / "logifine-api-tbd").mkdir()
    runner = FakeRunner(output="근거 없는 추측")
    agent = CodeAgentAnalysisAgent(runner=runner, project_resolver=ProjectResolver(root=tmp_path))

    result = agent.analyze("logifine-api-tbd core 모듈 분석해")

    assert result == insufficient_evidence_result()


def test_analyze_treats_readme_only_sources_as_insufficient_for_general_analysis(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "logifine-api-tbd"
    project_dir.mkdir()
    runner = FakeRunner(files_read=(project_dir / "README.md",))
    agent = CodeAgentAnalysisAgent(runner=runner, project_resolver=ProjectResolver(root=tmp_path))

    result = agent.analyze("logifine-api-tbd core 모듈 분석해")

    assert result == insufficient_evidence_result()


def test_analyze_accepts_readme_only_sources_for_a_readme_summary_request(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "logifine-api-tbd"
    project_dir.mkdir()
    runner = FakeRunner(files_read=(project_dir / "README.md",))
    agent = CodeAgentAnalysisAgent(runner=runner, project_resolver=ProjectResolver(root=tmp_path))

    result = agent.analyze("logifine-api-tbd README 요약해줘")

    assert result == AnalysisResult(summary="ok", findings=[], sources=["README.md"])
    assert len(runner.calls) == 1


def test_analyze_reports_a_timeout_as_a_limitation_instead_of_raising(tmp_path: Path) -> None:
    (tmp_path / "logifine-api-tbd").mkdir()
    runner = FakeRunner(error=RunnerTimeout())
    agent = CodeAgentAnalysisAgent(runner=runner, project_resolver=ProjectResolver(root=tmp_path))

    result = agent.analyze("logifine-api-tbd core 모듈 분석해")

    assert result.findings == []
    assert result.limitations == ["분석 시간 초과"]


def test_analyze_wraps_runner_errors_without_leaking_the_original_message(
    tmp_path: Path,
) -> None:
    (tmp_path / "logifine-api-tbd").mkdir()
    runner = FakeRunner(error=RunnerError("auth failed: sk-secret-token"))
    agent = CodeAgentAnalysisAgent(runner=runner, project_resolver=ProjectResolver(root=tmp_path))

    with pytest.raises(AnalysisAgentError) as exc_info:
        agent.analyze("logifine-api-tbd core 모듈 분석해")

    assert "sk-secret-token" not in str(exc_info.value)


def test_analyze_records_one_trace_step_with_runner_name_and_evidence_count(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "logifine-api-tbd"
    project_dir.mkdir()
    runner = FakeRunner(files_read=(project_dir / "A.java", project_dir / "B.java"))
    store = ThreadTraceStore()
    agent = CodeAgentAnalysisAgent(
        runner=runner,
        project_resolver=ProjectResolver(root=tmp_path),
        thread_trace_store=store,
    )

    agent.analyze("logifine-api-tbd core 모듈 분석해", channel_id="C1", thread_ts="1.0")

    trace = store.get("C1", "1.0")
    assert trace is not None
    assert trace.selected_project == "logifine-api-tbd"
    assert [(step.tool_name, step.outcome, len(step.evidence_refs)) for step in trace.steps] == [
        ("fake_runner", "ok", 2)
    ]


@pytest.mark.parametrize(
    ("runner_kwargs", "expected_outcome"),
    [
        ({}, "insufficient_evidence"),
        ({"error": RunnerTimeout()}, "timeout"),
        ({"error": RunnerError("secret")}, "error"),
    ],
)
def test_analyze_traces_unsuccessful_runs_with_their_outcome(
    tmp_path: Path, runner_kwargs: dict[str, Exception], expected_outcome: str
) -> None:
    (tmp_path / "logifine-api-tbd").mkdir()
    store = ThreadTraceStore()
    agent = CodeAgentAnalysisAgent(
        runner=FakeRunner(**runner_kwargs),  # type: ignore[arg-type]
        project_resolver=ProjectResolver(root=tmp_path),
        thread_trace_store=store,
    )

    with contextlib.suppress(AnalysisAgentError):
        agent.analyze("logifine-api-tbd core 모듈 분석해", channel_id="C1", thread_ts="1.0")

    trace = store.get("C1", "1.0")
    assert trace is not None
    assert [(step.tool_name, step.outcome) for step in trace.steps] == [
        ("fake_runner", expected_outcome)
    ]


def test_runner_prompt_instructs_read_only_source_reading_without_requiring_json(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "logifine-api-tbd"
    project_dir.mkdir()
    runner = FakeRunner(files_read=(project_dir / "Order.java",))
    agent = CodeAgentAnalysisAgent(runner=runner, project_resolver=ProjectResolver(root=tmp_path))

    agent.analyze("logifine-api-tbd core 모듈 분석해")

    prompt = runner.calls[0]
    assert "파일을 수정하지" in prompt
    assert "직접 읽" in prompt
    assert "JSON" not in prompt


_PLAN_JSON = (
    '{"goal": "g", "project_name": "demo", "affected_files": ["a.py"], '
    '"steps": [{"action": "write_file", "path": "a.py", "content": "x"}], '
    '"verification_commands": ["run_tests"], "risk": "modify"}'
)


def test_create_execution_plan_sends_plan_prompt_to_text_only_complete(tmp_path: Path) -> None:
    runner = FakeRunner(complete_output=_PLAN_JSON)
    agent = CodeAgentAnalysisAgent(runner=runner, project_resolver=ProjectResolver(root=tmp_path))
    context = ProjectContext(
        project_name="demo", root=tmp_path, instructions=[], git=GitState(is_repository=False)
    )
    existing = [ExistingFile(relative_path="a.py", content=None)]

    plan = agent.create_execution_plan("plan 짜줘", context, [], existing)

    assert runner.complete_calls == [build_code_plan_prompt("plan 짜줘", context, [], existing)]
    assert runner.calls == []
    assert plan.affected_files == ["a.py"]


def _plan_agent(
    tmp_path: Path, runner: FakeRunner, store: ThreadTraceStore | None = None
) -> tuple[CodeAgentAnalysisAgent, ProjectContext]:
    agent = CodeAgentAnalysisAgent(
        runner=runner,
        project_resolver=ProjectResolver(root=tmp_path),
        thread_trace_store=store,
    )
    context = ProjectContext(
        project_name="demo", root=tmp_path, instructions=[], git=GitState(is_repository=False)
    )
    return agent, context


def test_create_execution_plan_parses_a_fenced_json_reply(tmp_path: Path) -> None:
    runner = FakeRunner(complete_output=f"```json\n{_PLAN_JSON}\n```")
    agent, context = _plan_agent(tmp_path, runner)

    plan = agent.create_execution_plan("plan", context, [], [])

    assert plan.goal == "g"
    assert plan.risk is ExecutionRisk.MODIFY


def test_create_execution_plan_raises_format_error_on_malformed_reply(tmp_path: Path) -> None:
    agent, context = _plan_agent(tmp_path, FakeRunner(complete_output="계획은 다음과 같습니다"))

    with pytest.raises(PlanResponseFormatError):
        agent.create_execution_plan("plan", context, [], [])


@pytest.mark.parametrize("error", [RunnerTimeout(), RunnerError("SECRET sdk output")])
def test_create_execution_plan_hides_runner_failures(tmp_path: Path, error: Exception) -> None:
    agent, context = _plan_agent(tmp_path, FakeRunner(complete_error=error))

    with pytest.raises(AnalysisAgentError) as raised:
        agent.create_execution_plan("plan", context, [], [])

    assert "SECRET" not in str(raised.value)
    assert not isinstance(raised.value, PlanResponseFormatError)


_REPAIR_JSON = '{"steps": [{"action": "write_file", "path": "a.py", "content": "fixed"}]}'


def _repair_inputs() -> tuple[ExecutionPlan, list[ExistingFile], list[CommandResult]]:
    plan = ExecutionPlan(
        goal="g",
        project_name="demo",
        affected_files=["a.py"],
        steps=[],
        verification_commands=["run_tests"],
        risk=ExecutionRisk.MODIFY,
    )
    return plan, [ExistingFile("a.py", "broken")], [CommandResult("run_tests", False, "boom")]


def test_create_repair_steps_sends_repair_prompt_to_complete_and_parses_steps(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(complete_output=_REPAIR_JSON)
    agent, _ = _plan_agent(tmp_path, runner)

    steps = agent.create_repair_steps(*_repair_inputs())

    assert [(s.action, s.path, s.content) for s in steps] == [("write_file", "a.py", "fixed")]
    assert "boom" in runner.complete_calls[0]
    assert runner.calls == []


def test_create_repair_steps_raises_on_malformed_reply(tmp_path: Path) -> None:
    agent, _ = _plan_agent(tmp_path, FakeRunner(complete_output="not json"))

    with pytest.raises(AnalysisAgentError):
        agent.create_repair_steps(*_repair_inputs())


@pytest.mark.parametrize("error", [RunnerTimeout(), RunnerError("SECRET sdk output")])
def test_create_repair_steps_hides_runner_failures(tmp_path: Path, error: Exception) -> None:
    agent, _ = _plan_agent(tmp_path, FakeRunner(complete_error=error))

    with pytest.raises(AnalysisAgentError) as raised:
        agent.create_repair_steps(*_repair_inputs())

    assert "SECRET" not in str(raised.value)


def test_plan_and_repair_generation_each_record_a_content_free_trace_step(
    tmp_path: Path,
) -> None:
    store = ThreadTraceStore()
    runner = FakeRunner(name="claude_code", complete_output=_PLAN_JSON)
    agent, context = _plan_agent(tmp_path, runner, store)

    agent.create_execution_plan("plan", context, [], [], channel_id="C1", thread_ts="1.0")
    runner.complete_output = _REPAIR_JSON
    agent.create_repair_steps(*_repair_inputs(), channel_id="C1", thread_ts="1.0")
    runner.complete_error = RunnerTimeout()
    with pytest.raises(AnalysisAgentError):
        agent.create_repair_steps(*_repair_inputs(), channel_id="C1", thread_ts="1.0")

    trace = store.get("C1", "1.0")
    assert trace is not None
    assert [(s.phase, s.tool_name, s.outcome) for s in trace.steps] == [
        ("plan", "claude_code", "ok"),
        ("repair", "claude_code", "ok"),
        ("repair", "claude_code", "timeout"),
    ]
    assert all(s.input_fields == () and s.evidence_refs == () for s in trace.steps)

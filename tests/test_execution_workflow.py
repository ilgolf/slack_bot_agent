"""Confirmed, project-bounded execution plans for Slack code requests."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import pytest

from src.agent import AnalysisResult
from src.execution_workflow import (
    CommandResult,
    ExecutionPlan,
    ExecutionResult,
    ExecutionRisk,
    ExecutionStep,
    ExecutionWorkflow,
    ExistingFile,
    PendingPlanStatus,
    PendingPlanStore,
    ProjectContextLoader,
    ProjectExecutionTools,
    SkillRegistry,
    VerificationStatus,
    render_code_work_status,
    render_execution_result,
)
from src.project_resolver import ProjectResolver
from src.run_state import CodeWorkState, CodeWorkStateStore
from src.thread_context import ThreadContextStore


class PlanningAgent:
    def __init__(self, plan: ExecutionPlan) -> None:
        self.plan = plan
        self.calls = 0
        self.received_existing_files: list[ExistingFile] | None = None
        self.received_request: str | None = None

    def create_execution_plan(
        self,
        request: str,
        context: object,
        skills: object,
        existing_files: list[ExistingFile],
    ) -> ExecutionPlan:
        self.calls += 1
        self.received_existing_files = existing_files
        self.received_request = request
        return self.plan


class InvestigatingPlanningAgent(PlanningAgent):
    def __init__(self, plan: ExecutionPlan, sources: list[str]) -> None:
        super().__init__(plan)
        self.sources = sources
        self.questions: list[str] = []

    def analyze(
        self, question: str, *, channel_id: str = "-", thread_ts: str = "-"
    ) -> AnalysisResult:
        del channel_id, thread_ts
        self.questions.append(question)
        return AnalysisResult(summary="조사 완료", findings=[], sources=self.sources)


class StateAwarePlanningAgent(PlanningAgent):
    def __init__(self, plan: ExecutionPlan, state_store: CodeWorkStateStore) -> None:
        super().__init__(plan)
        self.state_store = state_store

    def create_execution_plan(
        self,
        request: str,
        context: object,
        skills: object,
        existing_files: list[ExistingFile],
    ) -> ExecutionPlan:
        assert self.state_store.state(channel_id="C1", thread_ts="1.1") is CodeWorkState.DISCOVERING
        return super().create_execution_plan(request, context, skills, existing_files)


class RepairingPlanningAgent(PlanningAgent):
    def __init__(self, plan: ExecutionPlan) -> None:
        super().__init__(plan)
        self.repair_calls = 0

    def create_repair_steps(
        self,
        plan: ExecutionPlan,
        existing_files: list[ExistingFile],
        checks: list[CommandResult],
    ) -> list[ExecutionStep]:
        self.repair_calls += 1
        assert plan.affected_files == ["README.md"]
        assert existing_files == [ExistingFile("README.md", "broken\n")]
        assert checks == [CommandResult("run_tests", False, "test failed")]
        return [ExecutionStep(action="write_file", path="README.md", content="fixed\n")]


class RepeatedlyFailingRepairAgent(PlanningAgent):
    def __init__(self, plan: ExecutionPlan) -> None:
        super().__init__(plan)
        self.repair_calls = 0

    def create_repair_steps(
        self,
        plan: ExecutionPlan,
        existing_files: list[ExistingFile],
        checks: list[CommandResult],
    ) -> list[ExecutionStep]:
        del plan, existing_files, checks
        self.repair_calls += 1
        return [
            ExecutionStep(
                action="write_file",
                path="README.md",
                content=f"still broken {self.repair_calls}\n",
            )
        ]


class SameDiffRepairAgent(PlanningAgent):
    def __init__(self, plan: ExecutionPlan) -> None:
        super().__init__(plan)
        self.repair_calls = 0

    def create_repair_steps(
        self,
        plan: ExecutionPlan,
        existing_files: list[ExistingFile],
        checks: list[CommandResult],
    ) -> list[ExecutionStep]:
        del plan, existing_files, checks
        self.repair_calls += 1
        return [ExecutionStep(action="write_file", path="README.md", content="same repair\n")]


class ProtectedFileRepairAgent(PlanningAgent):
    def create_repair_steps(
        self,
        plan: ExecutionPlan,
        existing_files: list[ExistingFile],
        checks: list[CommandResult],
    ) -> list[ExecutionStep]:
        del plan, existing_files, checks
        return [ExecutionStep(action="write_file", path="plan.md", content="# repair\n")]


class InvalidRepairAgent(PlanningAgent):
    def create_repair_steps(
        self,
        plan: ExecutionPlan,
        existing_files: list[ExistingFile],
        checks: list[CommandResult],
    ) -> list[ExecutionStep]:
        del plan, existing_files, checks
        raise RuntimeError("invalid model response")


def _plan(*, content: str = "after\n", checks: list[str] | None = None) -> ExecutionPlan:
    return ExecutionPlan(
        goal="README를 갱신합니다",
        project_name="my-project",
        affected_files=["README.md"],
        steps=[ExecutionStep(action="write_file", path="README.md", content=content)],
        verification_commands=checks or [],
        risk=ExecutionRisk.MODIFY,
    )


def test_code_work_status_messages_are_deterministic_and_content_safe() -> None:
    assert render_code_work_status(CodeWorkState.DISCOVERING) == "🔎 코드 구조를 확인 중입니다…"
    assert render_code_work_status(CodeWorkState.AWAITING_CONFIRMATION) == (
        "📝 구현 계획을 만들었습니다. 실행 확인을 기다립니다."
    )
    assert render_code_work_status(CodeWorkState.IMPLEMENTING) == (
        "🛠️ 코드 변경을 적용했습니다. 검증 중입니다…"
    )
    assert render_code_work_status(CodeWorkState.VERIFYING) == (
        "🛠️ 코드 변경을 적용했습니다. 검증 중입니다…"
    )
    assert render_code_work_status(CodeWorkState.REPAIRING, repair_attempt=2) == (
        "🔁 테스트 실패를 분석해 수정 중입니다… (2/2)"
    )
    assert render_code_work_status(CodeWorkState.SUCCEEDED) == "✅ 구현 및 검증 완료"
    assert render_code_work_status(CodeWorkState.FAILED) == "❌ 구현 실패"


def _workflow(tmp_path: Path, *, plan_store: PendingPlanStore | None = None) -> ExecutionWorkflow:
    return ExecutionWorkflow(
        project_resolver=ProjectResolver(root=tmp_path),
        plan_store=plan_store,
    )


def test_workflow_reads_target_file_content_before_creating_plan(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "README.md").write_text("before\n")
    agent = PlanningAgent(_plan())

    _workflow(tmp_path).process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert agent.received_existing_files is not None
    contents = {item.relative_path: item.content for item in agent.received_existing_files}
    assert contents == {"README.md": "before\n"}


def test_workflow_transitions_from_discovery_to_awaiting_confirmation(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "README.md").write_text("before\n")
    state_store = CodeWorkStateStore()
    workflow = ExecutionWorkflow(
        project_resolver=ProjectResolver(root=tmp_path),
        code_work_state_store=state_store,
    )
    agent = StateAwarePlanningAgent(_plan(), state_store)

    response = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert response is not None
    assert state_store.state(channel_id="C1", thread_ts="1.1") is (
        CodeWorkState.AWAITING_CONFIRMATION
    )


def test_execution_transitions_from_implementing_to_succeeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "README.md").write_text("before\n")
    state_store = CodeWorkStateStore()
    workflow = ExecutionWorkflow(
        project_resolver=ProjectResolver(root=tmp_path),
        code_work_state_store=state_store,
    )
    original_write_file = ProjectExecutionTools.write_file

    def write_file_while_implementing(
        tools: ProjectExecutionTools, relative_path: str, content: str
    ) -> tuple[str, str]:
        assert state_store.state(channel_id="C1", thread_ts="1.1") is CodeWorkState.IMPLEMENTING
        return original_write_file(tools, relative_path, content)

    monkeypatch.setattr(ProjectExecutionTools, "write_file", write_file_while_implementing)
    context = ThreadContextStore(root=tmp_path / "context")
    agent = PlanningAgent(_plan())
    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=context,
        agent=agent,
    )

    response = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="실행",
        thread_context=context,
        agent=agent,
    )

    assert response is not None
    assert state_store.state(channel_id="C1", thread_ts="1.1") is CodeWorkState.SUCCEEDED


def test_execution_transitions_to_verifying_before_running_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "README.md").write_text("before\n")
    state_store = CodeWorkStateStore()
    workflow = ExecutionWorkflow(
        project_resolver=ProjectResolver(root=tmp_path),
        code_work_state_store=state_store,
    )

    def run_check_while_verifying(tools: ProjectExecutionTools, name: str) -> CommandResult:
        assert state_store.state(channel_id="C1", thread_ts="1.1") is CodeWorkState.VERIFYING
        return CommandResult(name=name, success=True, output="")

    monkeypatch.setattr(ProjectExecutionTools, "run_check", run_check_while_verifying)
    context = ThreadContextStore(root=tmp_path / "context")
    agent = PlanningAgent(_plan(checks=["run_tests"]))
    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=context,
        agent=agent,
    )

    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="실행",
        thread_context=context,
        agent=agent,
    )

    assert state_store.state(channel_id="C1", thread_ts="1.1") is CodeWorkState.SUCCEEDED


def test_execution_records_failed_state_when_verification_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "README.md").write_text("before\n")
    state_store = CodeWorkStateStore()
    workflow = ExecutionWorkflow(
        project_resolver=ProjectResolver(root=tmp_path),
        code_work_state_store=state_store,
    )
    monkeypatch.setattr(
        ProjectExecutionTools,
        "run_check",
        lambda _tools, name: CommandResult(name=name, success=False, output="failed"),
    )
    context = ThreadContextStore(root=tmp_path / "context")
    agent = PlanningAgent(_plan(checks=["run_tests"]))
    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=context,
        agent=agent,
    )

    response = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="실행",
        thread_context=context,
        agent=agent,
    )

    assert response is not None
    assert "검증 실패" in response
    assert state_store.state(channel_id="C1", thread_ts="1.1") is CodeWorkState.FAILED


def test_command_result_derives_a_structured_verification_status() -> None:
    assert CommandResult("run_tests", True, "").status is VerificationStatus.SUCCEEDED
    assert CommandResult("run_tests", False, "failed").status is VerificationStatus.FAILED


def test_execution_records_failed_state_when_writing_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "README.md").write_text("before\n")
    state_store = CodeWorkStateStore()
    workflow = ExecutionWorkflow(
        project_resolver=ProjectResolver(root=tmp_path),
        code_work_state_store=state_store,
    )
    monkeypatch.setattr(
        ProjectExecutionTools,
        "write_file",
        lambda _tools, _path, _content: (_ for _ in ()).throw(OSError("disk unavailable")),
    )
    context = ThreadContextStore(root=tmp_path / "context")
    agent = PlanningAgent(_plan())
    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=context,
        agent=agent,
    )

    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="실행",
        thread_context=context,
        agent=agent,
    )

    assert state_store.state(channel_id="C1", thread_ts="1.1") is CodeWorkState.FAILED


def test_execution_repairs_a_failed_check_once_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    readme = project / "README.md"
    readme.write_text("before\n")
    checks = iter(
        [
            CommandResult("run_tests", False, "test failed"),
            CommandResult("run_tests", True, ""),
        ]
    )
    monkeypatch.setattr(ProjectExecutionTools, "run_check", lambda _tools, _name: next(checks))
    agent = RepairingPlanningAgent(_plan(content="broken\n", checks=["run_tests"]))
    workflow = _workflow(tmp_path)
    context = ThreadContextStore(root=tmp_path / "context")
    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=context,
        agent=agent,
    )

    response = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="실행",
        thread_context=context,
        agent=agent,
    )

    assert response is not None
    assert "✅ 구현 완료 (자동 복구 1회)" in response
    assert agent.repair_calls == 1
    assert readme.read_text() == "fixed\n"


def test_execution_stops_after_the_configured_repair_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "README.md").write_text("before\n")
    checks = iter(["failure one", "failure two", "failure three"])
    monkeypatch.setattr(
        ProjectExecutionTools,
        "run_check",
        lambda _tools, name: CommandResult(name, False, next(checks)),
    )
    agent = RepeatedlyFailingRepairAgent(_plan(checks=["run_tests"]))
    workflow = _workflow(tmp_path)
    context = ThreadContextStore(root=tmp_path / "context")
    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=context,
        agent=agent,
    )

    response = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="실행",
        thread_context=context,
        agent=agent,
    )

    assert response is not None
    assert "❌ 구현 실패: 자동 복구 한도 초과" in response
    assert agent.repair_calls == 2


def test_execution_stops_when_the_same_verification_failure_repeats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "README.md").write_text("before\n")
    monkeypatch.setattr(
        ProjectExecutionTools,
        "run_check",
        lambda _tools, name: CommandResult(name, False, f"failure {name}"),
    )
    agent = RepeatedlyFailingRepairAgent(_plan(checks=["run_tests"]))
    workflow = _workflow(tmp_path)
    context = ThreadContextStore(root=tmp_path / "context")
    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=context,
        agent=agent,
    )

    response = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="실행",
        thread_context=context,
        agent=agent,
    )

    assert response is not None
    assert "동일한 검증 실패가 반복되었습니다" in response
    assert agent.repair_calls == 1


def test_execution_stops_when_the_same_repair_diff_repeats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "README.md").write_text("before\n")
    failures = iter(["failure one", "failure two"])
    monkeypatch.setattr(
        ProjectExecutionTools,
        "run_check",
        lambda _tools, name: CommandResult(name, False, next(failures)),
    )
    agent = SameDiffRepairAgent(_plan(checks=["run_tests"]))
    workflow = _workflow(tmp_path)
    context = ThreadContextStore(root=tmp_path / "context")
    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=context,
        agent=agent,
    )

    response = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="실행",
        thread_context=context,
        agent=agent,
    )

    assert response is not None
    assert "동일한 자동 복구 diff가 반복되었습니다" in response
    assert agent.repair_calls == 2


def test_execution_returns_safe_failure_when_repair_planner_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "README.md").write_text("before\n")
    monkeypatch.setattr(
        ProjectExecutionTools,
        "run_check",
        lambda _tools, name: CommandResult(name, False, "test failed"),
    )
    workflow = _workflow(tmp_path)
    context = ThreadContextStore(root=tmp_path / "context")
    agent = InvalidRepairAgent(_plan(checks=["run_tests"]))
    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=context,
        agent=agent,
    )

    response = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="실행",
        thread_context=context,
        agent=agent,
    )

    assert response is not None
    assert "자동 복구 계획을 해석하지 못했습니다" in response


def test_repair_requires_new_confirmation_before_editing_a_protected_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    protected = project / "plan.md"
    protected.write_text("# before\n")
    monkeypatch.setattr(
        ProjectExecutionTools,
        "run_check",
        lambda _tools, name: CommandResult(name, False, "failure"),
    )
    plan = ExecutionPlan(
        goal="plan 갱신",
        project_name="my-project",
        affected_files=["plan.md"],
        steps=[ExecutionStep(action="write_file", path="plan.md", content="# confirmed\n")],
        verification_commands=["run_tests"],
        risk=ExecutionRisk.MODIFY,
    )
    workflow = _workflow(tmp_path)
    context = ThreadContextStore(root=tmp_path / "context")
    agent = ProtectedFileRepairAgent(plan)
    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project plan.md 수정해줘",
        thread_context=context,
        agent=agent,
    )

    response = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="실행",
        thread_context=context,
        agent=agent,
    )

    assert response is not None
    assert "보호 파일은 자동 복구할 수 없어 새 계획과 확인이 필요합니다" in response
    assert protected.read_text() == "# confirmed\n"


def test_pending_plans_and_confirmation_are_isolated_per_slack_thread(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    first = project / "a.txt"
    second = project / "b.txt"
    first.write_text("a-before\n")
    second.write_text("b-before\n")
    workflow = _workflow(tmp_path)
    context = ThreadContextStore(root=tmp_path / "context")
    first_plan = ExecutionPlan(
        "a 수정", "my-project", ["a.txt"],
        [ExecutionStep("write_file", "a.txt", "a-after\n")], [], ExecutionRisk.MODIFY
    )
    second_plan = ExecutionPlan(
        "b 수정", "my-project", ["b.txt"],
        [ExecutionStep("write_file", "b.txt", "b-after\n")], [], ExecutionRisk.MODIFY
    )
    workflow.process(
        channel_id="C1", thread_ts="T1", text="my-project a.txt 수정해줘",
        thread_context=context, agent=PlanningAgent(first_plan)
    )
    workflow.process(
        channel_id="C1", thread_ts="T2", text="my-project b.txt 수정해줘",
        thread_context=context, agent=PlanningAgent(second_plan)
    )

    workflow.process(
        channel_id="C1", thread_ts="T1", text="실행", thread_context=context,
        agent=PlanningAgent(first_plan)
    )

    assert first.read_text() == "a-after\n"
    assert second.read_text() == "b-before\n"
    assert not workflow.has_pending("C1", "T1")
    assert workflow.has_pending("C1", "T2")


def test_workflow_grounds_a_general_code_plan_in_agent_read_sources(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    source = project / "src" / "auth.py"
    source.parent.mkdir()
    source.write_text("def authenticate() -> bool:\n    return True\n")
    plan = ExecutionPlan(
        goal="인증 반환값 수정",
        project_name="my-project",
        affected_files=["src/auth.py"],
        steps=[
            ExecutionStep(
                action="write_file",
                path="src/auth.py",
                content="def authenticate() -> bool:\n    return False\n",
            )
        ],
        verification_commands=[],
        risk=ExecutionRisk.MODIFY,
    )
    agent = InvestigatingPlanningAgent(plan, sources=["src/auth.py"])

    workflow = _workflow(tmp_path)
    result = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project 인증 수정해줘",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert result is not None
    assert agent.questions == ["my-project 인증 수정해줘"]
    assert agent.received_existing_files == [
        ExistingFile(relative_path="src/auth.py", content=source.read_text())
    ]


def test_explicit_file_plan_does_not_require_exploration_sources(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "plan.md").write_text("# existing plan\n")
    plan = ExecutionPlan(
        goal="Update the plan",
        project_name="my-project",
        affected_files=["plan.md"],
        steps=[ExecutionStep(action="write_file", path="plan.md", content="# updated plan\n")],
        verification_commands=[],
        risk=ExecutionRisk.MODIFY,
    )
    agent = InvestigatingPlanningAgent(plan, sources=[])

    result = _workflow(tmp_path).process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project plan.md에 Linear 연동 작업 계획을 작성해줘",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert result is not None
    assert "코드 실행 계획" in result
    assert agent.questions == []
    assert agent.received_existing_files == [
        ExistingFile(relative_path="plan.md", content="# existing plan\n")
    ]


def test_workflow_extracts_target_path_from_thread_context_not_just_current_message(
    tmp_path: Path,
) -> None:
    """A file named earlier in the thread must still be picked up when a
    later message doesn't repeat it — `target_paths` extraction must look at
    thread context the same way `_project_name` already does."""
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "docs").mkdir()
    (project / "docs" / "x.md").write_text("existing docs\n")
    thread_context = ThreadContextStore(root=tmp_path / "context")
    thread_context.append("C1", "1.1", "my-project docs/x.md 파일에 대한 이야기였어")
    agent = PlanningAgent(_plan())

    _workflow(tmp_path).process(
        channel_id="C1",
        thread_ts="1.1",
        text="설명 추가해줘",
        thread_context=thread_context,
        agent=agent,
    )

    assert agent.received_existing_files is not None
    contents = {item.relative_path: item.content for item in agent.received_existing_files}
    assert contents == {"docs/x.md": "existing docs\n"}


def test_workflow_includes_thread_context_in_the_planning_request_text(tmp_path: Path) -> None:
    """The model must see earlier thread messages when planning, not just the
    latest one — otherwise a constraint agreed on earlier in the thread
    (e.g. "이 파일로 해줘") is invisible to the plan-creation prompt."""
    project = tmp_path / "my-project"
    project.mkdir()
    thread_context = ThreadContextStore(root=tmp_path / "context")
    thread_context.append("C1", "1.1", "my-project GitHub 연동 작업 plan 부터 짜볼래?")
    agent = PlanningAgent(_plan())

    _workflow(tmp_path).process(
        channel_id="C1",
        thread_ts="1.1",
        text="README.md 수정해줘",
        thread_context=thread_context,
        agent=agent,
    )

    assert agent.received_request is not None
    assert "GitHub 연동 작업" in agent.received_request
    assert "README.md 수정해줘" in agent.received_request


def test_workflow_resumes_after_missing_project_name_is_supplied_next(tmp_path: Path) -> None:
    """The bot's own "프로젝트명을 알려주세요" reply must not orphan the
    original code-work request — the next message supplying just the project
    name must resume it, not be judged as a fresh, markerless message."""
    project = tmp_path / "my-project"
    project.mkdir()
    thread_context = ThreadContextStore(root=tmp_path / "context")
    workflow = _workflow(tmp_path)
    agent = PlanningAgent(_plan())  # project_name="my-project", affected_files=["README.md"]

    first = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="GitHub 연동 작업 plan부터 짜볼래?",
        thread_context=thread_context,
        agent=agent,
    )
    assert first == "코드 작업할 대상 프로젝트명을 요청에 포함해 주세요."
    # Mirrors what RequestCoordinator.process() does unconditionally after
    # every turn — both the user's message and the bot's reply are recorded.
    thread_context.append("C1", "1.1", "GitHub 연동 작업 plan부터 짜볼래?")
    thread_context.append("C1", "1.1", first)

    second = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project",
        thread_context=thread_context,
        agent=agent,
    )

    assert second is not None
    assert "코드 실행 계획" in second
    assert agent.received_request is not None
    assert "GitHub 연동 작업" in agent.received_request


def test_workflow_replans_on_a_bare_file_mention_when_a_plan_is_already_pending(
    tmp_path: Path,
) -> None:
    """Once a plan preview exists for a thread, naming a different file is
    enough to redirect it — no modification verb required. Without a pending
    plan, the same bare mention must still be ignored (no regression)."""
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "docs").mkdir()
    (project / "docs" / "b.md").write_text("existing b\n")
    thread_context = ThreadContextStore(root=tmp_path / "context")
    workflow = _workflow(tmp_path)
    first_agent = PlanningAgent(_plan())  # affected_files=["README.md"]

    preview = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=thread_context,
        agent=first_agent,
    )
    assert preview is not None
    assert workflow.has_pending("C1", "1.1")
    thread_context.append("C1", "1.1", "my-project README.md 수정해줘")

    second_plan = ExecutionPlan(
        goal="b.md로 변경",
        project_name="my-project",
        affected_files=["docs/b.md"],
        steps=[ExecutionStep(action="write_file", path="docs/b.md", content="new b\n")],
        verification_commands=[],
        risk=ExecutionRisk.MODIFY,
    )
    second_agent = PlanningAgent(second_plan)

    result = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="docs/b.md 파일로",
        thread_context=thread_context,
        agent=second_agent,
    )

    assert result is not None
    assert "코드 실행 계획" in result
    assert "docs/b.md" in result
    assert second_agent.received_existing_files is not None
    contents = {item.relative_path: item.content for item in second_agent.received_existing_files}
    assert contents == {"docs/b.md": "existing b\n"}


def test_workflow_ignores_bare_file_mention_without_a_pending_plan(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    thread_context = ThreadContextStore(root=tmp_path / "context")
    thread_context.append("C1", "1.1", "my-project 관련 이야기")
    agent = PlanningAgent(_plan())

    result = _workflow(tmp_path).process(
        channel_id="C1",
        thread_ts="1.1",
        text="docs/b.md 파일로",
        thread_context=thread_context,
        agent=agent,
    )

    assert result is None
    assert agent.calls == 0


def test_workflow_asks_for_clarification_on_ambiguous_message_with_pending_plan(
    tmp_path: Path,
) -> None:
    """A bare follow-up ("작업해") that's neither a confirm/cancel keyword nor
    a redirect trigger must not be silently dropped when a plan is already
    pending — it should ask what to do next, referencing what's pending."""
    project = tmp_path / "my-project"
    project.mkdir()
    thread_context = ThreadContextStore(root=tmp_path / "context")
    workflow = _workflow(tmp_path)
    agent = PlanningAgent(_plan())  # goal="README를 갱신합니다", affected_files=["README.md"]

    preview = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=thread_context,
        agent=agent,
    )
    assert preview is not None

    result = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="작업해",
        thread_context=thread_context,
        agent=agent,
    )

    assert result is not None
    assert "보류 중인" in result
    assert "README.md" in result
    assert "실행" in result
    assert workflow.has_pending("C1", "1.1")


def test_workflow_does_not_ask_for_clarification_without_a_pending_plan(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    agent = PlanningAgent(_plan())

    result = _workflow(tmp_path).process(
        channel_id="C1",
        thread_ts="1.1",
        text="작업해",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert result is None
    assert agent.calls == 0


def test_workflow_marks_a_not_yet_existing_target_file_as_no_content(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    agent = PlanningAgent(
        ExecutionPlan(
            goal="새 파일 생성",
            project_name="my-project",
            affected_files=["new_feature.py"],
            steps=[ExecutionStep(action="write_file", path="new_feature.py", content="pass\n")],
            verification_commands=[],
            risk=ExecutionRisk.MODIFY,
        )
    )

    _workflow(tmp_path).process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project new_feature.py 구현해줘",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert agent.received_existing_files is not None
    contents = {item.relative_path: item.content for item in agent.received_existing_files}
    assert contents == {"new_feature.py": None}


def test_workflow_allows_planning_without_a_named_file_when_model_proposes_a_new_one(
    tmp_path: Path,
) -> None:
    """A "plan first" request (e.g. a multi-file integration) may not name any
    file up front — the model may propose one during planning. A brand-new
    file has nothing to re-read, so this takes exactly one planning call."""
    project = tmp_path / "my-project"
    project.mkdir()
    agent = PlanningAgent(
        ExecutionPlan(
            goal="GitHub 연동 초안",
            project_name="my-project",
            affected_files=["docs/GITHUB_INTEGRATION.md"],
            steps=[
                ExecutionStep(
                    action="write_file",
                    path="docs/GITHUB_INTEGRATION.md",
                    content="# GitHub 연동 계획\n",
                )
            ],
            verification_commands=[],
            risk=ExecutionRisk.MODIFY,
        )
    )

    result = _workflow(tmp_path).process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project GitHub 연동 작업 plan 부터 짜볼래?",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert result is not None
    assert "코드 실행 계획" in result
    assert agent.calls == 1


def test_workflow_regrounds_plan_when_model_proposes_an_existing_file_without_a_target(
    tmp_path: Path,
) -> None:
    """When the model's proposed file (chosen without any named target)
    already exists, the workflow must re-plan with its real content instead
    of leaving the model's first, blind guess as the final plan."""
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "README.md").write_text("existing project docs\n")
    agent = PlanningAgent(_plan())  # proposes affected_files=["README.md"]

    result = _workflow(tmp_path).process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project GitHub 연동 작업 plan 부터 짜볼래?",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert result is not None
    assert agent.calls == 2
    assert agent.received_existing_files is not None
    contents = {item.relative_path: item.content for item in agent.received_existing_files}
    assert contents == {"README.md": "existing project docs\n"}


def test_workflow_rejects_a_model_proposed_edit_to_a_project_control_file(
    tmp_path: Path,
) -> None:
    """A vague "plan first" request with no named file must never let the
    model quietly choose to rewrite the project's own `plan.md`/`CLAUDE.md`/
    `AGENTS.md` — those are only editable when the user names them
    explicitly, not when the model invents a target on its own."""
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "plan.md").write_text("# real plan, do not overwrite\n")
    agent = PlanningAgent(
        ExecutionPlan(
            goal="Fabricated Phase 7",
            project_name="my-project",
            affected_files=["plan.md"],
            steps=[
                ExecutionStep(action="write_file", path="plan.md", content="# fabricated\n")
            ],
            verification_commands=[],
            risk=ExecutionRisk.MODIFY,
        )
    )
    workflow = _workflow(tmp_path)

    result = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project GitHub 연동 작업 plan 부터 짜볼래?",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert result is not None
    assert "plan.md" in result
    assert not workflow.has_pending("C1", "1.1")
    assert (project / "plan.md").read_text() == "# real plan, do not overwrite\n"


def test_workflow_allows_project_control_file_edit_when_user_names_it(tmp_path: Path) -> None:
    """The same file is fair game once the user names it explicitly — the
    guard is about the model inventing the target, not the file itself."""
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "plan.md").write_text("# real plan\n")
    agent = PlanningAgent(
        ExecutionPlan(
            goal="Update plan.md",
            project_name="my-project",
            affected_files=["plan.md"],
            steps=[ExecutionStep(action="write_file", path="plan.md", content="# updated\n")],
            verification_commands=[],
            risk=ExecutionRisk.MODIFY,
        )
    )

    result = _workflow(tmp_path).process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project plan.md 수정해줘",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert result is not None
    assert "코드 실행 계획" in result


def test_plan_follow_request_does_not_authorize_plan_file_overwrite(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "plan.md").write_text("# implementation specification\n")
    agent = PlanningAgent(
        ExecutionPlan(
            goal="Rewrite plan.md",
            project_name="my-project",
            affected_files=["plan.md"],
            steps=[ExecutionStep(action="write_file", path="plan.md", content="# rewritten\n")],
            verification_commands=[],
            risk=ExecutionRisk.MODIFY,
        )
    )

    workflow = _workflow(tmp_path)
    result = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project plan.md에 적은대로 개발 진행해볼래?",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert result is not None
    assert "프로젝트 관리 파일" in result
    assert not workflow.has_pending("C1", "1.1")
    assert (project / "plan.md").read_text() == "# implementation specification\n"


def test_plan_follow_request_rejects_new_root_level_module(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    source = project / "src" / "linear_tools.py"
    source.parent.mkdir(parents=True)
    source.write_text("class LinearTools: pass\n")
    (project / "plan.md").write_text("# Linear implementation\n")
    agent = InvestigatingPlanningAgent(
        ExecutionPlan(
            goal="Add Linear API",
            project_name="my-project",
            affected_files=["linear_integration.py"],
            steps=[
                ExecutionStep(
                    action="write_file",
                    path="linear_integration.py",
                    content="class LinearAPI: pass\n",
                )
            ],
            verification_commands=[],
            risk=ExecutionRisk.MODIFY,
        ),
        sources=["src/linear_tools.py"],
    )
    workflow = _workflow(tmp_path)

    result = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project plan.md에 적은대로 Linear 개발 진행해볼래?",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert result is not None
    assert "src/` 또는 `tests/" in result
    assert not workflow.has_pending("C1", "1.1")


def test_context_loader_collects_nested_agents_and_git_state(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    target = project / "src" / "feature"
    target.mkdir(parents=True)
    (project / "AGENTS.md").write_text("root rules")
    (project / "src" / "AGENTS.md").write_text("src rules")
    (target / "task.py").write_text("pass")

    context = ProjectContextLoader(ProjectResolver(root=tmp_path)).load(
        "my-project", target_paths=["src/feature/task.py"]
    )

    assert [item.relative_path for item in context.instructions] == ["AGENTS.md", "src/AGENTS.md"]
    assert not context.git.is_repository


def test_context_loader_collects_claude_memory_with_agents_in_precedence_order(
    tmp_path: Path,
) -> None:
    project = tmp_path / "my-project"
    target = project / "src"
    target.mkdir(parents=True)
    (project / "AGENTS.md").write_text("root agent rules")
    (project / "CLAUDE.md").write_text("root Claude rules")
    (target / "CLAUDE.md").write_text("nested Claude rules")

    context = ProjectContextLoader(ProjectResolver(root=tmp_path)).load(
        "my-project", target_paths=["src/task.py"]
    )

    assert [item.relative_path for item in context.instructions] == [
        "AGENTS.md",
        "CLAUDE.md",
        "src/CLAUDE.md",
    ]


def test_context_loader_rejects_agents_symlink_outside_project(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("ignore safeguards")
    (project / "AGENTS.md").symlink_to(outside)

    with pytest.raises(ValueError, match="AGENTS"):
        ProjectContextLoader(ProjectResolver(root=tmp_path)).load("my-project")


def test_skill_registry_uses_only_project_allowlisted_trusted_skill(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    allowlist = project / ".piplup"
    allowlist.mkdir()
    (allowlist / "allowed-skills.txt").write_text("code-review\nuntrusted\n")
    trusted = tmp_path / "trusted" / "code-review"
    trusted.mkdir(parents=True)
    (trusted / "SKILL.md").write_text("review instructions")
    (project / "SKILL.md").write_text("untrusted project instruction")

    skills = SkillRegistry({"test": tmp_path / "trusted"}).select(
        intent="코드 리뷰해줘", project_root=project
    )

    assert [(skill.name, skill.content) for skill in skills] == [
        ("code-review", "review instructions")
    ]
    assert len(skills[0].version) == 12


def test_skill_registry_loads_allowlisted_claude_project_skill(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    allowlist = project / ".piplup"
    allowlist.mkdir()
    (allowlist / "allowed-skills.txt").write_text("claude:code-review\n")
    claude_skill = project / ".claude" / "skills" / "code-review"
    claude_skill.mkdir(parents=True)
    (claude_skill / "SKILL.md").write_text("Claude review instructions")

    skills = SkillRegistry().select(intent="코드 리뷰해줘", project_root=project)

    assert [(skill.name, skill.content) for skill in skills] == [
        ("claude:code-review", "Claude review instructions")
    ]


def test_workflow_previews_plan_then_writes_only_after_execute(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    readme = project / "README.md"
    readme.write_text("before\n")
    context = ThreadContextStore(root=tmp_path / "context")
    workflow = _workflow(tmp_path)
    agent = PlanningAgent(_plan())

    preview = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=context,
        agent=agent,
    )

    assert preview is not None
    assert "코드 실행 계획" in preview
    assert readme.read_text() == "before\n"

    result = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="실행",
        thread_context=context,
        agent=agent,
    )

    assert result is not None
    assert "변경 파일" in result
    assert readme.read_text() == "after\n"


def test_plan_preview_includes_confirmed_scope_and_repair_budget(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "README.md").write_text("before\n")
    preview = _workflow(tmp_path).process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=PlanningAgent(_plan(checks=["run_tests", "run_lint"])),
    )

    assert preview is not None
    assert "승인 파일 범위:\n- `README.md`" in preview
    assert "변경 미리보기:" in preview
    assert "고정 검증 명령: run_tests, run_lint" in preview
    assert "자동 복구 예산: 최대 2회" in preview
    assert "미지원 작업: 임의 셸·HTTP·패키지 설치/삭제·rename·Git commit/push" in preview
    assert "남은 위험:" in preview


def test_execution_end_to_end_reports_failed_verification_not_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A verification command failing must never render as a success, even
    though the write step itself already succeeded."""
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "README.md").write_text("before\n")

    def fake_run(
        command: Sequence[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, returncode=1, stdout="", stderr="test failed")

    monkeypatch.setattr("src.execution_workflow.subprocess.run", fake_run)
    agent = PlanningAgent(_plan(checks=["run_tests"]))
    workflow = _workflow(tmp_path)
    context = ThreadContextStore(root=tmp_path / "context")
    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=context,
        agent=agent,
    )

    result = workflow.process(
        channel_id="C1", thread_ts="1.1", text="실행", thread_context=context, agent=agent
    )

    assert result is not None
    assert "✅" not in result
    assert "⚠️ 변경 적용, 검증 실패" in result
    assert (project / "README.md").read_text() == "after\n"


def test_execution_reports_verification_timeout_not_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A verification command that times out must be reported as a timeout,
    never silently rendered as a passing check."""
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "README.md").write_text("before\n")

    def fake_run(
        command: Sequence[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=command, timeout=120)

    monkeypatch.setattr("src.execution_workflow.subprocess.run", fake_run)
    agent = PlanningAgent(_plan(checks=["run_tests"]))
    workflow = _workflow(tmp_path)
    context = ThreadContextStore(root=tmp_path / "context")
    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=context,
        agent=agent,
    )

    result = workflow.process(
        channel_id="C1", thread_ts="1.1", text="실행", thread_context=context, agent=agent
    )

    assert result is not None
    assert "✅" not in result
    assert "시간 초과" in result
    assert (project / "README.md").read_text() == "after\n"


def test_workflow_preview_includes_unified_diff_excerpt(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "README.md").write_text("before\n")
    agent = PlanningAgent(_plan(content="after\n"))

    preview = _workflow(tmp_path).process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert preview is not None
    assert "-before" in preview
    assert "+after" in preview


def test_execution_applies_each_write_step_and_writes_both_files(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "a.txt").write_text("a-before\n")
    (project / "b.txt").write_text("b-before\n")
    plan = ExecutionPlan(
        goal="두 파일 수정",
        project_name="my-project",
        affected_files=["a.txt", "b.txt"],
        steps=[
            ExecutionStep(action="write_file", path="a.txt", content="a-after\n"),
            ExecutionStep(action="write_file", path="b.txt", content="b-after\n"),
        ],
        verification_commands=[],
        risk=ExecutionRisk.MODIFY,
    )
    agent = PlanningAgent(plan)
    workflow = _workflow(tmp_path)
    context = ThreadContextStore(root=tmp_path / "context")
    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project a.txt b.txt 수정해줘",
        thread_context=context,
        agent=agent,
    )

    result = workflow.process(
        channel_id="C1", thread_ts="1.1", text="실행", thread_context=context, agent=agent
    )

    assert result is not None
    assert (project / "a.txt").read_text() == "a-after\n"
    assert (project / "b.txt").read_text() == "b-after\n"
    assert "`a.txt`" in result
    assert "`b.txt`" in result


def test_workflow_requires_a_new_confirmation_for_a_plan_outside_approved_scope(
    tmp_path: Path,
) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    readme = project / "README.md"
    other = project / "other.md"
    readme.write_text("before\n")
    other.write_text("other-before\n")
    out_of_scope_plan = ExecutionPlan(
        goal="두 파일 수정",
        project_name="my-project",
        affected_files=["README.md"],
        steps=[
            ExecutionStep(action="write_file", path="README.md", content="after\n"),
            ExecutionStep(action="write_file", path="other.md", content="other-after\n"),
        ],
        verification_commands=[],
        risk=ExecutionRisk.MODIFY,
    )
    workflow = _workflow(tmp_path)

    response = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=PlanningAgent(out_of_scope_plan),
    )

    assert response is not None
    assert "승인 파일 범위 밖 수정" in response
    assert "새 계획과 실행 확인이 필요" in response
    assert not workflow.has_pending("C1", "1.1")
    assert readme.read_text() == "before\n"
    assert other.read_text() == "other-before\n"


def test_execution_reports_already_applied_files_when_a_later_step_fails(
    tmp_path: Path,
) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "a.txt").write_text("a-before\n")
    (project / "b.txt").write_text("b-before\n")
    oversized_content = "x" * 2_000_000  # exceeds ProjectExecutionTools' write size limit
    plan = ExecutionPlan(
        goal="두 파일 수정",
        project_name="my-project",
        affected_files=["a.txt", "b.txt"],
        steps=[
            ExecutionStep(action="write_file", path="a.txt", content="a-after\n"),
            ExecutionStep(action="write_file", path="b.txt", content=oversized_content),
        ],
        verification_commands=[],
        risk=ExecutionRisk.MODIFY,
    )
    agent = PlanningAgent(plan)
    workflow = _workflow(tmp_path)
    context = ThreadContextStore(root=tmp_path / "context")
    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project a.txt b.txt 수정해줘",
        thread_context=context,
        agent=agent,
    )

    result = workflow.process(
        channel_id="C1", thread_ts="1.1", text="실행", thread_context=context, agent=agent
    )

    assert result is not None
    assert "실행 실패" in result
    # the first step must have been applied and reported before the second
    # step's failure stopped the remaining steps.
    assert (project / "a.txt").read_text() == "a-after\n"
    assert (project / "b.txt").read_text() == "b-before\n"
    assert "`a.txt`" in result
    assert "`b.txt`" not in result


def test_workflow_cancels_only_pending_plan_without_writing(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    readme = project / "README.md"
    readme.write_text("before\n")
    context = ThreadContextStore(root=tmp_path / "context")
    workflow = _workflow(tmp_path)
    agent = PlanningAgent(_plan())
    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=context,
        agent=agent,
    )

    response = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="취소",
        thread_context=context,
        agent=agent,
    )

    assert response is not None
    assert "취소" in response
    assert readme.read_text() == "before\n"
    assert (
        workflow.process(
            channel_id="C1",
            thread_ts="1.1",
            text="실행",
            thread_context=context,
            agent=agent,
        )
        == "실행할 보류 계획이 없습니다. 먼저 코드 작업 요청을 보내 주세요."
    )


def test_expired_pending_plan_is_not_executed(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "README.md").write_text("before\n")
    context = ThreadContextStore(root=tmp_path / "context")
    workflow = _workflow(tmp_path, plan_store=PendingPlanStore(ttl=timedelta(seconds=-1)))
    agent = PlanningAgent(_plan())
    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=context,
        agent=agent,
    )

    result = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="실행",
        thread_context=context,
        agent=agent,
    )

    assert result is not None
    assert "만료" in result
    assert (project / "README.md").read_text() == "before\n"


def test_execution_tools_reject_path_escape_and_unplanned_write(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    tools = ProjectExecutionTools(project, ["README.md"])

    with pytest.raises(ValueError, match="계획에 포함되지"):
        tools.write_file("other.md", "no")
    with pytest.raises(ValueError, match="허용되지 않은"):
        tools.read_file("../secret.txt")


def test_workflow_rejects_arbitrary_verification_command(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "README.md").write_text("before\n")
    bad_plan = _plan(checks=["run_tests; curl https://example.com"])
    result = _workflow(tmp_path).process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=PlanningAgent(bad_plan),
    )

    assert result is not None
    assert "허용되지 않은 검증 명령" in result
    assert (project / "README.md").read_text() == "before\n"


def test_pending_store_deduplicates_same_request(tmp_path: Path) -> None:
    store = PendingPlanStore()
    plan = _plan()

    first = store.put("C1", "1.1", plan, request="same")
    second = store.put("C1", "1.1", plan, request="same")
    status, taken = store.take("C1", "1.1")

    assert first is second
    assert status is PendingPlanStatus.READY
    assert taken is not None


def test_pending_store_peek_returns_plan_without_consuming_it() -> None:
    """A clarification message needs to read the pending plan's content
    without a `실행`/`취소` reply having to happen — peeking must not consume
    the confirmation the way `take`/`cancel` do."""
    store = PendingPlanStore()
    plan = _plan()
    store.put("C1", "1.1", plan, request="req")

    peeked = store.peek("C1", "1.1")

    assert peeked is plan
    assert store.has_pending("C1", "1.1")


def test_pending_store_peek_returns_none_for_missing_or_expired() -> None:
    store = PendingPlanStore(ttl=timedelta(seconds=-1))
    store.put("C1", "1.1", _plan(), request="req")

    assert PendingPlanStore().peek("C1", "1.1") is None
    assert store.peek("C1", "1.1") is None


def test_execution_report_marks_failed_check_and_redacts_secret() -> None:
    response = render_execution_result(
        _plan(checks=["run_tests"]),
        ExecutionResult(
            changed_files=["README.md"],
            diffs=["--- a/README.md\n+++ b/README.md\n-old\n+new\n"],
            checks=[CommandResult("run_tests", False, "API_TOKEN=do-not-show failed")],
            remaining_risks=["실패한 검증을 수정한 뒤 새 계획을 만들어 다시 실행해야 합니다."],
        ),
    )

    assert "⚠️ 변경 적용, 검증 실패" in response
    assert "API_TOKEN=[REDACTED]" in response
    assert "do-not-show" not in response
    assert "Diff 요약: +1/-1줄" in response


def test_execution_reports_missing_verification_runner_after_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing local runner must produce a Slack-safe final state, rather
    than escaping the listener after the approved file write has happened."""
    project = tmp_path / "my-project"
    project.mkdir()
    readme = project / "README.md"
    readme.write_text("before\n")
    plan = _plan(checks=["run_tests"])
    workflow = _workflow(tmp_path)
    context = ThreadContextStore(root=tmp_path / "context")
    monkeypatch.setitem(
        ProjectExecutionTools.COMMANDS,
        "run_tests",
        ("missing-verification-runner",),
    )

    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=context,
        agent=PlanningAgent(plan),
    )
    result = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="실행",
        thread_context=context,
        agent=PlanningAgent(plan),
    )

    assert result is not None
    assert "⚠️ 변경 적용, 검증 실패" in result
    assert "실행 환경 오류" in result
    assert "검증 도구를 시작할 수 없습니다 (FileNotFoundError)." in result
    assert readme.read_text() == "after\n"

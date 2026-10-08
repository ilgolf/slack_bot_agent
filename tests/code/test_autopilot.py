"""plan.md autopilot: run every unchecked item without per-item confirmation."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.code.plan import ExecutionPlan, ExecutionRisk, ExecutionStep, next_unchecked_item
from src.code.tooluse import CommandResult, ProjectExecutionTools
from src.code.workflow import ExecutionWorkflow, _is_autopilot_request
from src.core.config import Settings
from src.core.project_resolver import ProjectResolver
from src.linear.workflow import LinearIntegrationWorkflow
from src.slack.request_coordinator import RequestCoordinator
from src.slack.thread_context import ThreadContextStore
from tests.router_doubles import WordGatedRouter


class PlanningAgent:
    def __init__(self, plan: ExecutionPlan) -> None:
        self.plan = plan

    def create_execution_plan(
        self, request: str, context: object, skills: object, existing_files: object
    ) -> ExecutionPlan:
        return self.plan


def test_end_to_end_plan_follow_request_is_autopilot() -> None:
    assert _is_autopilot_request("plan.md 기준으로 끝까지 진행해")


def test_single_item_plan_follow_request_is_not_autopilot() -> None:
    assert not _is_autopilot_request("plan.md 기준으로 코드 작성 진행해")


def test_next_unchecked_item_returns_first_unchecked_in_file_order() -> None:
    plan = "# Plan\n- [x] done\n- [ ] first todo\n- [ ] second todo\n"

    assert next_unchecked_item(plan) == "first todo"


def test_next_unchecked_item_returns_none_when_everything_is_checked() -> None:
    assert next_unchecked_item("# Plan\n- [x] done\n- [x] also done\n") is None


def test_autopilot_request_executes_plan_without_confirmation(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "plan.md").write_text("- [ ] update readme\n")
    (project / "README.md").write_text("before\n")
    agent = PlanningAgent(
        ExecutionPlan(
            goal="README를 갱신합니다",
            project_name="my-project",
            affected_files=["README.md"],
            steps=[ExecutionStep(action="write_file", path="README.md", content="after\n")],
            verification_commands=[],
            risk=ExecutionRisk.MODIFY,
        )
    )
    workflow = ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path))

    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project plan.md 기준으로 끝까지 진행해",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert (project / "README.md").read_text() == "after\n"
    assert not workflow.has_pending("C1", "1.1")


class SequencedPlanningAgent:
    def __init__(self, plans: list[ExecutionPlan]) -> None:
        self.plans = plans
        self.calls = 0

    def create_execution_plan(
        self, request: str, context: object, skills: object, existing_files: object
    ) -> ExecutionPlan:
        plan = self.plans[self.calls]
        self.calls += 1
        return plan


def _write_plan(path: str, content: str) -> ExecutionPlan:
    return ExecutionPlan(
        goal=f"{path} 작성",
        project_name="my-project",
        affected_files=[path],
        steps=[ExecutionStep(action="write_file", path=path, content=content)],
        verification_commands=[],
        risk=ExecutionRisk.MODIFY,
    )


def test_autopilot_runs_next_unchecked_item_after_a_verified_item(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "plan.md").write_text("- [ ] first\n- [ ] second\n")
    agent = SequencedPlanningAgent(
        [_write_plan("src/a.txt", "A\n"), _write_plan("src/b.txt", "B\n")]
    )
    workflow = ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path))

    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project plan.md 기준으로 끝까지 진행해",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert (project / "src" / "a.txt").read_text() == "A\n"
    assert (project / "src" / "b.txt").read_text() == "B\n"
    assert agent.calls == 2
    assert next_unchecked_item((project / "plan.md").read_text()) is None


def test_autopilot_reports_all_done_when_no_unchecked_items_remain(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "plan.md").write_text("- [ ] first\n- [ ] second\n")
    agent = SequencedPlanningAgent(
        [_write_plan("src/a.txt", "A\n"), _write_plan("src/b.txt", "B\n")]
    )
    workflow = ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path))

    result = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project plan.md 기준으로 끝까지 진행해",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert result is not None
    assert "전체 완료" in result
    assert "2/2" in result


def test_autopilot_stops_and_reports_failed_item_when_verification_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "pyproject.toml").write_text("")
    (project / "plan.md").write_text("- [ ] first\n- [ ] second\n")
    failing = _write_plan("src/a.txt", "A\n")
    failing.verification_commands.append("run_tests")
    agent = SequencedPlanningAgent([failing, _write_plan("src/b.txt", "B\n")])
    monkeypatch.setattr(
        ProjectExecutionTools,
        "run_check",
        lambda _tools, name: CommandResult(name, False, "1 failed"),
    )
    workflow = ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path))

    result = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project plan.md 기준으로 끝까지 진행해",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert result is not None
    assert "중단" in result
    assert "first" in result
    assert not (project / "src" / "b.txt").exists()
    assert next_unchecked_item((project / "plan.md").read_text()) == "first"


def test_autopilot_rejects_protected_meta_file_plan_and_stops(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "plan.md").write_text("- [ ] first\n- [ ] second\n")
    (project / "CLAUDE.md").write_text("rules\n")
    agent = SequencedPlanningAgent(
        [_write_plan("CLAUDE.md", "hijacked\n"), _write_plan("src/b.txt", "B\n")]
    )
    workflow = ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path))

    result = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project plan.md 기준으로 끝까지 진행해",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert result is not None
    assert "중단" in result
    assert (project / "CLAUDE.md").read_text() == "rules\n"
    assert not (project / "src" / "b.txt").exists()
    assert agent.calls == 1


def test_autopilot_rejects_path_outside_project_and_stops(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "plan.md").write_text("- [ ] first\n- [ ] second\n")
    agent = SequencedPlanningAgent(
        [_write_plan("../outside.txt", "x\n"), _write_plan("src/b.txt", "B\n")]
    )
    workflow = ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path))

    result = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project plan.md 기준으로 끝까지 진행해",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert result is not None
    assert "중단" in result
    assert not (tmp_path / "outside.txt").exists()
    assert agent.calls == 1


def test_autopilot_stops_at_max_items_and_reports_remaining(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "plan.md").write_text("- [ ] one\n- [ ] two\n- [ ] three\n")
    agent = SequencedPlanningAgent(
        [
            _write_plan("src/a.txt", "A\n"),
            _write_plan("src/b.txt", "B\n"),
            _write_plan("src/c.txt", "C\n"),
        ]
    )
    workflow = ExecutionWorkflow(
        project_resolver=ProjectResolver(root=tmp_path), max_autopilot_items=2
    )

    result = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project plan.md 기준으로 끝까지 진행해",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert result is not None
    assert "최대 2개" in result
    assert "남은 항목 1개" in result
    assert not (project / "src" / "c.txt").exists()
    assert agent.calls == 2


class CancellingPlanningAgent(SequencedPlanningAgent):
    """Simulates the user sending `취소` while the first item is being planned."""

    def __init__(self, plans: list[ExecutionPlan], workflow_ref: list[ExecutionWorkflow]) -> None:
        super().__init__(plans)
        self.workflow_ref = workflow_ref
        self.cancel_reply: str | None = None

    def create_execution_plan(
        self, request: str, context: object, skills: object, existing_files: object
    ) -> ExecutionPlan:
        if self.calls == 0:
            self.cancel_reply = self.workflow_ref[0].process(
                channel_id="C1",
                thread_ts="1.1",
                text="취소",
                thread_context=ThreadContextStore(root=Path("/nonexistent")),
                agent=self,
            )
        return super().create_execution_plan(request, context, skills, existing_files)


def test_cancel_during_autopilot_stops_after_current_item(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "plan.md").write_text("- [ ] first\n- [ ] second\n")
    workflow_ref: list[ExecutionWorkflow] = []
    agent = CancellingPlanningAgent(
        [_write_plan("src/a.txt", "A\n"), _write_plan("src/b.txt", "B\n")], workflow_ref
    )
    workflow = ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path))
    workflow_ref.append(workflow)

    result = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project plan.md 기준으로 끝까지 진행해",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert agent.cancel_reply is not None
    assert "취소" in agent.cancel_reply
    assert result is not None
    assert "취소" in result
    assert (project / "src" / "a.txt").read_text() == "A\n"
    assert not (project / "src" / "b.txt").exists()
    assert agent.calls == 1


def test_autopilot_reports_progress_after_each_completed_item(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "plan.md").write_text("- [ ] first\n- [ ] second\n")
    agent = SequencedPlanningAgent(
        [_write_plan("src/a.txt", "A\n"), _write_plan("src/b.txt", "B\n")]
    )
    workflow = ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path))
    progress: list[str] = []

    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project plan.md 기준으로 끝까지 진행해",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
        on_progress=progress.append,
    )

    assert progress == ["🔄 자동 진행 1/2 완료", "🔄 자동 진행 2/2 완료"]


def test_coordinator_forwards_progress_to_code_work_workflow(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "plan.md").write_text("- [ ] first\n- [ ] second\n")
    agent = SequencedPlanningAgent(
        [_write_plan("src/a.txt", "A\n"), _write_plan("src/b.txt", "B\n")]
    )
    coordinator = RequestCoordinator(
        router=WordGatedRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
        linear_workflow=LinearIntegrationWorkflow(settings=Settings(linear_api_key=None)),
    )
    progress: list[str] = []

    coordinator.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project plan.md 기준으로 끝까지 진행해",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,  # type: ignore[arg-type]
        on_progress=progress.append,
    )

    assert progress == ["🔄 자동 진행 1/2 완료", "🔄 자동 진행 2/2 완료"]


def test_autopilot_summary_lists_each_item_with_its_changed_files(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "plan.md").write_text("- [ ] first\n- [ ] second\n")
    agent = SequencedPlanningAgent(
        [_write_plan("src/a.txt", "A\n"), _write_plan("src/b.txt", "B\n")]
    )
    workflow = ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path))

    result = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project plan.md 기준으로 끝까지 진행해",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert result is not None
    assert "전체 완료 (2/2개 항목)" in result
    assert "- ✅ first: `src/a.txt`" in result
    assert "- ✅ second: `src/b.txt`" in result


def test_autopilot_summary_includes_total_diff_and_per_item_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "pyproject.toml").write_text("")
    (project / "plan.md").write_text("- [ ] first\n- [ ] second\n")
    first, second = _write_plan("src/a.txt", "A\n"), _write_plan("src/b.txt", "B\n")
    first.verification_commands.append("run_tests")
    second.verification_commands.append("run_tests")
    agent = SequencedPlanningAgent([first, second])
    monkeypatch.setattr(
        ProjectExecutionTools, "run_check", lambda _tools, name: CommandResult(name, True, "ok")
    )
    workflow = ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path))

    result = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project plan.md 기준으로 끝까지 진행해",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert result is not None
    assert "Diff 요약: +2/-0줄" in result
    assert result.count("  - ✅ run_tests") == 2


class DuplicateRequestingPlanningAgent(SequencedPlanningAgent):
    """Simulates a second autopilot request arriving while the first item runs."""

    def __init__(self, plans: list[ExecutionPlan], workflow_ref: list[ExecutionWorkflow]) -> None:
        super().__init__(plans)
        self.workflow_ref = workflow_ref
        self.duplicate_reply: str | None = None

    def create_execution_plan(
        self, request: str, context: object, skills: object, existing_files: object
    ) -> ExecutionPlan:
        if self.calls == 0:
            self.duplicate_reply = self.workflow_ref[0].process(
                channel_id="C1",
                thread_ts="1.1",
                text="my-project plan.md 기준으로 끝까지 진행해",
                thread_context=ThreadContextStore(root=Path("/nonexistent")),
                agent=self,
            )
        return super().create_execution_plan(request, context, skills, existing_files)


def test_duplicate_autopilot_request_in_same_thread_is_ignored(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "plan.md").write_text("- [ ] first\n- [ ] second\n")
    workflow_ref: list[ExecutionWorkflow] = []
    agent = DuplicateRequestingPlanningAgent(
        [_write_plan("src/a.txt", "A\n"), _write_plan("src/b.txt", "B\n")], workflow_ref
    )
    workflow = ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path))
    workflow_ref.append(workflow)

    result = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project plan.md 기준으로 끝까지 진행해",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert agent.duplicate_reply is not None
    assert "이미 자동 진행 중" in agent.duplicate_reply
    assert agent.calls == 2
    assert result is not None
    assert "전체 완료 (2/2개 항목)" in result


def test_autopilot_summary_marks_items_that_ran_without_verification(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "plan.md").write_text("- [ ] first\n")
    agent = SequencedPlanningAgent([_write_plan("src/a.txt", "A\n")])
    workflow = ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path))

    result = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project plan.md 기준으로 끝까지 진행해",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=agent,
    )

    assert result is not None
    assert "- ✅ first: `src/a.txt` (검증 없음)" in result

"""Confirmed, project-bounded execution plans for Slack code requests."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from src.execution_workflow import (
    CommandResult,
    ExecutionPlan,
    ExecutionResult,
    ExecutionRisk,
    ExecutionStep,
    ExecutionWorkflow,
    PendingPlanStatus,
    PendingPlanStore,
    ProjectContextLoader,
    ProjectExecutionTools,
    SkillRegistry,
    render_execution_result,
)
from src.project_resolver import ProjectResolver
from src.thread_context import ThreadContextStore


class PlanningAgent:
    def __init__(self, plan: ExecutionPlan) -> None:
        self.plan = plan
        self.calls = 0

    def create_execution_plan(self, request: str, context: object, skills: object) -> ExecutionPlan:
        self.calls += 1
        return self.plan


def _plan(*, content: str = "after\n", checks: list[str] | None = None) -> ExecutionPlan:
    return ExecutionPlan(
        goal="README를 갱신합니다",
        project_name="my-project",
        affected_files=["README.md"],
        steps=[ExecutionStep(action="write_file", path="README.md", content=content)],
        verification_commands=checks or [],
        risk=ExecutionRisk.MODIFY,
    )


def _workflow(tmp_path: Path, *, plan_store: PendingPlanStore | None = None) -> ExecutionWorkflow:
    return ExecutionWorkflow(
        project_resolver=ProjectResolver(root=tmp_path),
        plan_store=plan_store,
    )


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
    assert workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="실행",
        thread_context=context,
        agent=agent,
    ) == "실행할 보류 계획이 없습니다. 먼저 코드 작업 요청을 보내 주세요."


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

    assert "⚠️ 실행 완료, 검증 실패" in response
    assert "API_TOKEN=[REDACTED]" in response
    assert "do-not-show" not in response
    assert "Diff 요약: +1/-1줄" in response

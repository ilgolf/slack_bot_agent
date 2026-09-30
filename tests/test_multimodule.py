"""Multi-module, non-Python projects: code paths are not only top-level src/ and tests/."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from src.execution_workflow import (
    ExecutionPlan,
    ExecutionRisk,
    ExecutionStep,
    _is_source_or_test_path,
    _remaining_risks,
    _validate_plan,
)


def test_module_source_path_is_a_code_path() -> None:
    assert _is_source_or_test_path("api/src/main/java/X.java")


def test_module_test_path_is_a_code_path() -> None:
    assert _is_source_or_test_path("api/src/test/java/XTest.java")


def test_documentation_path_is_not_a_code_path() -> None:
    assert not _is_source_or_test_path("docs/readme.md")


def _module_plan(path: str) -> ExecutionPlan:
    return ExecutionPlan(
        goal="모듈 코드를 수정합니다",
        project_name="logifine-api-tbd",
        affected_files=[path],
        steps=[ExecutionStep(action="write_file", path=path, content="class X {}\n")],
        verification_commands=[],
        risk=ExecutionRisk.MODIFY,
    )


def test_plan_follow_accepts_module_source_as_implementation_evidence() -> None:
    path = "api/src/main/java/X.java"

    _validate_plan(
        _module_plan(path),
        "logifine-api-tbd",
        [],
        evidence_paths=[path],
        require_code_evidence=True,
    )


def test_plan_follow_accepts_new_file_under_module_src(tmp_path: Path) -> None:
    path = "api/src/main/java/NewService.java"

    _validate_plan(
        _module_plan(path),
        "logifine-api-tbd",
        [],
        project_root=tmp_path,
        restrict_new_files_to_code_roots=True,
    )


def test_plan_prompt_follows_existing_module_layout_instead_of_fixed_src() -> None:
    from src.langchain_agent import _CODE_PLAN_POLICY

    assert "src/ 아래" not in _CODE_PLAN_POLICY
    assert "모듈" in _CODE_PLAN_POLICY


def test_plan_for_project_without_pyproject_drops_verification_commands(tmp_path: Path) -> None:
    from src.execution_workflow import ExecutionWorkflow
    from src.project_resolver import ProjectResolver
    from src.thread_context import ThreadContextStore

    project = tmp_path / "logifine-api-tbd"
    (project / "api" / "src" / "main").mkdir(parents=True)
    (project / "build.gradle").write_text("plugins {}\n")
    plan = replace(_module_plan("api/src/main/X.java"), verification_commands=["run_tests"])

    class Agent:
        def create_execution_plan(self, *_args: object) -> ExecutionPlan:
            return plan

    workflow = ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path))
    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="logifine-api-tbd api/src/main/X.java 수정해줘",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=Agent(),
    )

    pending = workflow.plan_store.peek("C1", "1.1")
    assert pending is not None
    assert pending.verification_commands == []


def test_plan_for_project_with_pyproject_keeps_verification_commands(tmp_path: Path) -> None:
    from src.execution_workflow import ExecutionWorkflow
    from src.project_resolver import ProjectResolver
    from src.thread_context import ThreadContextStore

    project = tmp_path / "py-project"
    project.mkdir()
    (project / "pyproject.toml").write_text("")
    (project / "app.py").write_text("x = 1\n")
    plan = replace(
        _module_plan("app.py"), project_name="py-project", verification_commands=["run_tests"]
    )

    class Agent:
        def create_execution_plan(self, *_args: object) -> ExecutionPlan:
            return plan

    workflow = ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path))
    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="py-project app.py 수정해줘",
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=Agent(),
    )

    pending = workflow.plan_store.peek("C1", "1.1")
    assert pending is not None
    assert pending.verification_commands == ["run_tests"]


def test_plan_preview_warns_when_no_verification_will_run() -> None:
    from src.execution_workflow import GitState, render_plan_preview

    preview = render_plan_preview(_module_plan("api/src/main/X.java"), GitState(True, []))

    assert "남은 위험: 자동 검증 없이 적용됩니다" in preview


def test_execution_result_warns_when_no_verification_ran() -> None:
    from src.execution_workflow import ExecutionResult, render_execution_result

    result = ExecutionResult(
        changed_files=["api/src/main/X.java"],
        diffs=[],
        checks=[],
        remaining_risks=_remaining_risks([]),
    )

    rendered = render_execution_result(_module_plan("api/src/main/X.java"), result)

    assert "자동 검증 없이 적용됐습니다" in rendered

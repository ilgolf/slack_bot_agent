"""Confirmed code plans stay in the execution workflow, never analysis fallback."""

from __future__ import annotations

from pathlib import Path

from src.agent import AnalysisResult
from src.agent_trace import AgentTraceRecorder, ThreadTraceStore
from src.artifact_generation import ArtifactDraft, ArtifactGenerationWorkflow
from src.config import Settings
from src.execution_workflow import ExecutionPlan, ExecutionRisk, ExecutionStep, ExecutionWorkflow
from src.linear_workflow import LinearIntegrationWorkflow
from src.project_resolver import ProjectResolver
from src.request_coordinator import RequestCoordinator
from src.request_router import RequestRouter
from src.thread_context import ThreadContextStore


class TraceAgent:
    def __init__(self) -> None:
        self.thread_trace_store = ThreadTraceStore()

    def analyze(
        self, question: str, *, channel_id: str = "-", thread_ts: str = "-"
    ) -> AnalysisResult:
        del question, channel_id, thread_ts
        return AnalysisResult(summary="요약", findings=[])


class PlanningAgent:
    def create_execution_plan(
        self, request: str, context: object, skills: object, existing_files: object
    ) -> ExecutionPlan:
        return ExecutionPlan(
            goal="README 수정",
            project_name="my-project",
            affected_files=["README.md"],
            steps=[ExecutionStep(action="write_file", path="README.md", content="after\n")],
            verification_commands=[],
            risk=ExecutionRisk.MODIFY,
        )


class InvestigatingPlanningAgent(PlanningAgent):
    """A planning agent that can also investigate code, for plan-follow
    requests that require evidence before a plan is created."""

    def __init__(self, sources: list[str]) -> None:
        self.sources = sources

    def analyze(
        self, question: str, *, channel_id: str = "-", thread_ts: str = "-"
    ) -> AnalysisResult:
        del question, channel_id, thread_ts
        return AnalysisResult(summary="조사 완료", findings=[], sources=self.sources)

    def create_execution_plan(
        self, request: str, context: object, skills: object, existing_files: object
    ) -> ExecutionPlan:
        del request, skills, existing_files
        return ExecutionPlan(
            goal="기능 구현",
            project_name=context.project_name,  # type: ignore[attr-defined]
            affected_files=["src/feature.py"],
            steps=[
                ExecutionStep(
                    action="write_file",
                    path="src/feature.py",
                    content="def feature():\n    return 2\n",
                )
            ],
            verification_commands=[],
            risk=ExecutionRisk.MODIFY,
        )


def test_fresh_thread_plan_follow_up_phrase_reaches_code_work_without_prior_context(
    tmp_path: Path,
) -> None:
    """"plan.md 보고 작업 진행해줘" names plan.md directly, so it must reach
    the execution workflow even as the very first message in a brand-new
    thread — unlike the vague pronoun phrases ("이 계획 진행해"), it doesn't
    need a prior pending or completed code-work turn to disambiguate it."""
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "plan.md").write_text("# spec\n")
    source = project / "src" / "feature.py"
    source.parent.mkdir(parents=True)
    source.write_text("def feature():\n    return 1\n")
    coordinator = RequestCoordinator(
        router=RequestRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
        artifact_workflow=ArtifactGenerationWorkflow(),
        linear_workflow=LinearIntegrationWorkflow(settings=Settings(linear_api_key=None)),
    )
    context = ThreadContextStore(root=tmp_path / "context")
    agent = InvestigatingPlanningAgent(sources=["src/feature.py"])

    _, response = coordinator.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project plan.md 보고 작업 진행해줘",
        thread_context=context,
        agent=agent,
    )

    assert "코드 실행 계획" in response


def test_execute_confirmation_consumes_pending_code_plan(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    readme = project / "README.md"
    readme.write_text("before\n")
    coordinator = RequestCoordinator(
        router=RequestRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
        artifact_workflow=ArtifactGenerationWorkflow(),
        linear_workflow=LinearIntegrationWorkflow(settings=Settings(linear_api_key=None)),
    )
    context = ThreadContextStore(root=tmp_path / "context")
    agent = PlanningAgent()

    _, preview = coordinator.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=context,
        agent=agent,  # type: ignore[arg-type]
    )
    _, result = coordinator.process(
        channel_id="C1",
        thread_ts="1.1",
        text="실행",
        thread_context=context,
        agent=agent,  # type: ignore[arg-type]
    )

    assert "코드 실행 계획" in preview
    assert "구현 및 검증 완료" in result
    assert readme.read_text() == "after\n"


def test_design_question_preserves_pending_code_plan(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    readme = project / "README.md"
    readme.write_text("before\n")
    coordinator = RequestCoordinator(
        router=RequestRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
        artifact_workflow=ArtifactGenerationWorkflow(),
        linear_workflow=LinearIntegrationWorkflow(settings=Settings(linear_api_key=None)),
    )
    context = ThreadContextStore(root=tmp_path / "context")
    agent = PlanningAgent()

    coordinator.process(
        channel_id="C1", thread_ts="1.1", text="my-project README.md 수정해줘",
        thread_context=context, agent=agent,  # type: ignore[arg-type]
    )
    _, answer = coordinator.process(
        channel_id="C1", thread_ts="1.1", text="GraphQL 기반으로 설계한 거야?",
        thread_context=context, agent=agent,  # type: ignore[arg-type]
    )

    assert "GraphQL" in answer
    assert "보류 중인 실행 계획" not in answer
    assert coordinator.execution_workflow.has_pending("C1", "1.1")
    assert readme.read_text() == "before\n"

    _, execution = coordinator.process(
        channel_id="C1", thread_ts="1.1", text="실행",
        thread_context=context, agent=agent,  # type: ignore[arg-type]
    )
    assert "구현 및 검증 완료" in execution
    assert readme.read_text() == "after\n"


def test_ambiguous_follow_up_gets_clarification_instead_of_general_analysis(
    tmp_path: Path,
) -> None:
    """"작업해" alone matches no router marker, so it would normally fall
    through to general analysis — but a pending code plan must intercept it
    with a clarification instead of silently misrouting."""
    project = tmp_path / "my-project"
    project.mkdir()
    coordinator = RequestCoordinator(
        router=RequestRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
        artifact_workflow=ArtifactGenerationWorkflow(),
        linear_workflow=LinearIntegrationWorkflow(settings=Settings(linear_api_key=None)),
    )
    context = ThreadContextStore(root=tmp_path / "context")
    agent = PlanningAgent()

    _, preview = coordinator.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 수정해줘",
        thread_context=context,
        agent=agent,  # type: ignore[arg-type]
    )
    assert "코드 실행 계획" in preview

    _, follow_up = coordinator.process(
        channel_id="C1",
        thread_ts="1.1",
        text="작업해",
        thread_context=context,
        agent=agent,  # type: ignore[arg-type]
    )

    assert "보류 중인" in follow_up
    assert "README.md" in follow_up


class ArtifactAndAnalysisAgent:
    """Owns an artifact pending action but no code plan — the clarification
    gate must key off `execution_workflow.has_pending`, not "some workflow has
    something pending"."""

    def create_artifact_draft(self, thread_context: str, destination: Path) -> ArtifactDraft:
        del thread_context
        return ArtifactDraft(destination=destination, kind="text", content="draft")

    def analyze(
        self, question: str, *, channel_id: str = "-", thread_ts: str = "-"
    ) -> AnalysisResult:
        del channel_id, thread_ts
        return AnalysisResult(summary=f"분석: {question}", findings=[])


def test_ambiguous_follow_up_is_unaffected_by_an_unrelated_pending_artifact(
    tmp_path: Path,
) -> None:
    """A pending *artifact* draft (not a code plan) must not trigger the code
    plan's clarification — each workflow's pending action stays its own."""
    coordinator = RequestCoordinator(
        router=RequestRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
        artifact_workflow=ArtifactGenerationWorkflow(),
        linear_workflow=LinearIntegrationWorkflow(settings=Settings(linear_api_key=None)),
    )
    context = ThreadContextStore(root=tmp_path / "context")
    agent = ArtifactAndAnalysisAgent()

    _, preview = coordinator.process(
        channel_id="C1",
        thread_ts="1.1",
        text="이메일 목록 정리해줘",
        thread_context=context,
        agent=agent,
    )
    assert coordinator.artifact_workflow.has_pending("C1", "1.1")
    assert not coordinator.execution_workflow.has_pending("C1", "1.1")

    _, follow_up = coordinator.process(
        channel_id="C1",
        thread_ts="1.1",
        text="작업해",
        thread_context=context,
        agent=agent,
    )

    assert "보류 중인" not in follow_up
    assert "분석:" in follow_up


def test_bare_project_name_reply_resumes_a_stalled_code_work_request(tmp_path: Path) -> None:
    """The router alone can't classify a bare project name as code work, so
    the coordinator must give a stalled ("awaiting project name") request one
    more chance before falling back to general analysis."""
    project = tmp_path / "my-project"
    project.mkdir()
    coordinator = RequestCoordinator(
        router=RequestRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
        artifact_workflow=ArtifactGenerationWorkflow(),
        linear_workflow=LinearIntegrationWorkflow(settings=Settings(linear_api_key=None)),
    )
    context = ThreadContextStore(root=tmp_path / "context")
    agent = PlanningAgent()

    _, first = coordinator.process(
        channel_id="C1",
        thread_ts="1.1",
        text="GitHub 연동 작업 plan부터 짜볼래?",
        thread_context=context,
        agent=agent,  # type: ignore[arg-type]
    )
    assert first == "코드 작업할 대상 프로젝트명을 요청에 포함해 주세요."

    _, second = coordinator.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project",
        thread_context=context,
        agent=agent,  # type: ignore[arg-type]
    )

    assert "코드 실행 계획" in second


def test_completed_code_work_lets_natural_continuation_phrase_start_new_plan(
    tmp_path: Path,
) -> None:
    """After a code-work plan has already run to completion in this thread,
    a natural continuation phrase with none of the hard-coded markers must
    still reach the execution workflow instead of falling to analysis."""
    project = tmp_path / "my-project"
    project.mkdir()
    readme = project / "README.md"
    readme.write_text("before\n")
    coordinator = RequestCoordinator(
        router=RequestRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
        artifact_workflow=ArtifactGenerationWorkflow(),
        linear_workflow=LinearIntegrationWorkflow(settings=Settings(linear_api_key=None)),
    )
    context = ThreadContextStore(root=tmp_path / "context")
    agent = PlanningAgent()

    coordinator.process(
        channel_id="C1", thread_ts="1.1", text="my-project README.md 수정해줘",
        thread_context=context, agent=agent,  # type: ignore[arg-type]
    )
    coordinator.process(
        channel_id="C1", thread_ts="1.1", text="실행",
        thread_context=context, agent=agent,  # type: ignore[arg-type]
    )
    assert not coordinator.execution_workflow.has_pending("C1", "1.1")

    _, follow_up = coordinator.process(
        channel_id="C1", thread_ts="1.1", text="위 작업 이어서 해줘",
        thread_context=context, agent=agent,  # type: ignore[arg-type]
    )

    assert "코드 실행 계획" in follow_up


class MultiProjectPlanningAgent:
    """Returns a plan for whichever project the workflow actually resolved,
    unlike `PlanningAgent`'s single hard-coded project name."""

    def create_execution_plan(
        self, request: str, context: object, skills: object, existing_files: object
    ) -> ExecutionPlan:
        del request, skills, existing_files
        return ExecutionPlan(
            goal="README 수정",
            project_name=context.project_name,  # type: ignore[attr-defined]
            affected_files=["README.md"],
            steps=[ExecutionStep(action="write_file", path="README.md", content="after\n")],
            verification_commands=[],
            risk=ExecutionRisk.MODIFY,
        )


def test_ambiguous_project_after_multiple_completed_plans_asks_for_choice(
    tmp_path: Path,
) -> None:
    """Two different projects each got a completed code-work turn earlier in
    this thread. A natural continuation phrase naming neither must not
    silently guess one — it should list both candidates instead."""
    for name in ("project-a", "project-b"):
        project = tmp_path / name
        project.mkdir()
        (project / "README.md").write_text("before\n")
    coordinator = RequestCoordinator(
        router=RequestRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
        artifact_workflow=ArtifactGenerationWorkflow(),
        linear_workflow=LinearIntegrationWorkflow(settings=Settings(linear_api_key=None)),
    )
    context = ThreadContextStore(root=tmp_path / "context")
    agent = MultiProjectPlanningAgent()

    coordinator.process(
        channel_id="C1", thread_ts="1.1", text="project-a README.md 수정해줘",
        thread_context=context, agent=agent,  # type: ignore[arg-type]
    )
    coordinator.process(
        channel_id="C1", thread_ts="1.1", text="실행",
        thread_context=context, agent=agent,  # type: ignore[arg-type]
    )
    coordinator.process(
        channel_id="C1", thread_ts="1.1", text="project-b README.md 수정해줘",
        thread_context=context, agent=agent,  # type: ignore[arg-type]
    )
    coordinator.process(
        channel_id="C1", thread_ts="1.1", text="실행",
        thread_context=context, agent=agent,  # type: ignore[arg-type]
    )

    _, follow_up = coordinator.process(
        channel_id="C1", thread_ts="1.1", text="위 작업 이어서 해줘",
        thread_context=context, agent=agent,  # type: ignore[arg-type]
    )

    assert "project-a" in follow_up
    assert "project-b" in follow_up
    assert "코드 실행 계획" not in follow_up


def test_trace_summary_uses_current_thread_key_from_thread_trace_store(tmp_path: Path) -> None:
    """'trace 요약' must look up the *requesting* thread's own trace, not a
    single agent-wide slot shared by every thread."""
    coordinator = RequestCoordinator(
        router=RequestRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
        artifact_workflow=ArtifactGenerationWorkflow(),
        linear_workflow=LinearIntegrationWorkflow(settings=Settings(linear_api_key=None)),
    )
    context = ThreadContextStore(root=tmp_path / "context")
    agent = TraceAgent()
    trace = AgentTraceRecorder(request_id="req-1")
    trace.record_tool(phase="act", tool_name="read_file", category="project_read", outcome="ok")
    agent.thread_trace_store.put("C1", "T1", trace)

    _, own_summary = coordinator.process(
        channel_id="C1", thread_ts="T1", text="trace 요약", thread_context=context, agent=agent
    )
    _, other_summary = coordinator.process(
        channel_id="C2", thread_ts="T2", text="trace 요약", thread_context=context, agent=agent
    )

    assert "read_file" in own_summary
    assert "기록된 실행이 없습니다" in other_summary

"""Confirmed code plans stay in the execution workflow, never analysis fallback."""

from __future__ import annotations

from pathlib import Path

from src.code.agent import AnalysisResult
from src.code.plan import ExecutionPlan, ExecutionRisk, ExecutionStep
from src.code.workflow import ExecutionWorkflow
from src.core.agent_trace import AgentTraceRecorder, ThreadTraceStore
from src.core.config import Settings
from src.core.plan_first import PlanAnswer
from src.core.project_resolver import ProjectResolver
from src.linear.workflow import LinearIntegrationWorkflow
from src.slack.request_coordinator import RequestCoordinator
from src.slack.request_router import RequestIntent, RequestRouter
from src.slack.thread_context import ThreadContextStore
from tests.router_doubles import WordGatedRouter


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


class AnsweringPlanningAgent(PlanningAgent):
    """A planning agent that also answers questions with a fixed analysis."""

    def analyze(
        self, question: str, *, channel_id: str = "-", thread_ts: str = "-"
    ) -> AnalysisResult:
        del channel_id, thread_ts
        return AnalysisResult(summary=f"분석 답변: {question}", findings=[])


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
        router=WordGatedRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
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
        router=WordGatedRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
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
    assert "구현 완료 (자동 검증 없음)" in result
    assert readme.read_text() == "after\n"


def test_design_question_preserves_pending_code_plan(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    readme = project / "README.md"
    readme.write_text("before\n")
    coordinator = RequestCoordinator(
        router=WordGatedRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
        linear_workflow=LinearIntegrationWorkflow(settings=Settings(linear_api_key=None)),
    )
    context = ThreadContextStore(root=tmp_path / "context")
    agent = AnsweringPlanningAgent()

    coordinator.process(
        channel_id="C1", thread_ts="1.1", text="my-project README.md 수정해줘",
        thread_context=context, agent=agent,
    )
    _, answer = coordinator.process(
        channel_id="C1", thread_ts="1.1", text="GraphQL 기반으로 설계한 거야?",
        thread_context=context, agent=agent,
    )

    assert "GraphQL" in answer
    assert "보류 중인 실행 계획" not in answer
    assert coordinator.execution_workflow.has_pending("C1", "1.1")
    assert readme.read_text() == "before\n"

    _, execution = coordinator.process(
        channel_id="C1", thread_ts="1.1", text="실행",
        thread_context=context, agent=agent,
    )
    assert "구현 완료 (자동 검증 없음)" in execution
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
        router=WordGatedRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
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


def test_bare_project_name_reply_resumes_a_stalled_code_work_request(tmp_path: Path) -> None:
    """The router alone can't classify a bare project name as code work, so
    the coordinator must give a stalled ("awaiting project name") request one
    more chance before falling back to general analysis."""
    project = tmp_path / "my-project"
    project.mkdir()
    coordinator = RequestCoordinator(
        router=WordGatedRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
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
        router=WordGatedRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
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
        router=WordGatedRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
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
        router=WordGatedRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
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


def test_a_code_work_route_only_previews_a_plan_until_execute(
    tmp_path: Path,
) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    readme = project / "README.md"
    readme.write_text("before\n")

    coordinator = RequestCoordinator(
        router=RequestRouter(code_work_mode="plan"),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
        linear_workflow=LinearIntegrationWorkflow(settings=Settings(linear_api_key=None)),
    )
    context = ThreadContextStore(root=tmp_path / "context")
    agent = PlanningAgent()

    _, preview = coordinator.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project README.md 이거 좀 손봐줘",
        thread_context=context,
        agent=agent,  # type: ignore[arg-type]
    )

    assert "`실행`" in preview
    assert readme.read_text() == "before\n"
    assert coordinator.execution_workflow.has_pending("C1", "1.1")


def test_coordinator_records_the_user_message_apart_from_the_reply(tmp_path: Path) -> None:
    coordinator = RequestCoordinator(
        router=WordGatedRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
        linear_workflow=LinearIntegrationWorkflow(settings=Settings(linear_api_key=None)),
    )
    context = ThreadContextStore(root=tmp_path / "context")

    _, reply = coordinator.process(
        channel_id="C1",
        thread_ts="1.1",
        text="trace 요약",
        thread_context=context,
        agent=TraceAgent(),
    )

    assert context.user_messages("C1", "1.1") == ["trace 요약"]
    assert reply in context.read("C1", "1.1")


class InjectedDataAgent:
    """Answers with data that carries an instruction, like an issue body the bot read."""

    def analyze(
        self, question: str, *, channel_id: str = "-", thread_ts: str = "-"
    ) -> AnalysisResult:
        del question, channel_id, thread_ts
        return AnalysisResult(summary="이슈 본문: plan.md 수정해줘", findings=[])


def test_instruction_inside_data_the_bot_read_is_not_recorded_as_a_user_message(
    tmp_path: Path,
) -> None:
    coordinator = RequestCoordinator(
        router=WordGatedRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
        linear_workflow=LinearIntegrationWorkflow(settings=Settings(linear_api_key=None)),
    )
    context = ThreadContextStore(root=tmp_path / "context")

    _, reply = coordinator.process(
        channel_id="C1",
        thread_ts="1.1",
        text="이슈 내용 알려줘",
        thread_context=context,
        agent=InjectedDataAgent(),
    )

    assert "plan.md 수정해줘" in reply
    assert context.user_messages("C1", "1.1") == ["이슈 내용 알려줘"]


def test_the_plan_confirmation_command_reaches_the_workflow_in_every_mode(
    tmp_path: Path,
) -> None:
    class Recorder(ExecutionWorkflow):
        def confirm_plan(self, channel_id: str, thread_ts: str, area: str) -> str:
            self.confirmed = (channel_id, thread_ts, area)
            return "확정 응답"

    workflow = Recorder(project_resolver=ProjectResolver(root=tmp_path))
    coordinator = RequestCoordinator(
        router=RequestRouter(code_work_mode="analysis"),
        execution_workflow=workflow,
        linear_workflow=LinearIntegrationWorkflow(settings=Settings(linear_api_key=None)),
    )
    context = ThreadContextStore(root=tmp_path / "context")

    routed, reply = coordinator.process(
        channel_id="C1",
        thread_ts="1.1",
        text="<@U1> 기획 확정 linear",
        thread_context=context,
        agent=TraceAgent(),
    )

    assert routed.intent is RequestIntent.PLAN_CONFIRM
    assert reply == "확정 응답"
    assert workflow.confirmed == ("C1", "1.1", "linear")
    assert context.user_messages("C1", "1.1") == ["<@U1> 기획 확정 linear"]


def test_a_plan_review_answer_reaches_the_workflow(tmp_path: Path) -> None:
    class Recorder(ExecutionWorkflow):
        def answer_plan_review(self, channel_id: str, thread_ts: str, answer: PlanAnswer) -> str:
            self.answered = (channel_id, thread_ts, answer)
            return "기록 응답"

    workflow = Recorder(project_resolver=ProjectResolver(root=tmp_path))
    coordinator = RequestCoordinator(
        router=RequestRouter(code_work_mode="analysis"),
        execution_workflow=workflow,
        linear_workflow=LinearIntegrationWorkflow(settings=Settings(linear_api_key=None)),
    )
    context = ThreadContextStore(root=tmp_path / "context")

    routed, reply = coordinator.process(
        channel_id="C1",
        thread_ts="1.1",
        text="결정: 1개로 시작",
        thread_context=context,
        agent=TraceAgent(),
    )

    assert routed.intent is RequestIntent.PLAN_ANSWER and reply == "기록 응답"
    assert workflow.answered == ("C1", "1.1", PlanAnswer("decision", "1개로 시작"))


class _ReviewRecorder(ExecutionWorkflow):
    reviewing = False

    def has_plan_review(self, channel_id: str, thread_ts: str) -> bool:
        return self.reviewing

    def answer_plan_review(self, channel_id: str, thread_ts: str, answer: PlanAnswer) -> str:
        self.answered = answer
        return "번호 기록"


def _number_coordinator(tmp_path: Path, workflow: ExecutionWorkflow) -> RequestCoordinator:
    return RequestCoordinator(
        router=RequestRouter(code_work_mode="analysis"),
        execution_workflow=workflow,
        linear_workflow=LinearIntegrationWorkflow(settings=Settings(linear_api_key=None)),
    )


def test_a_bare_number_answers_the_open_plan_review(tmp_path: Path) -> None:
    workflow = _ReviewRecorder(project_resolver=ProjectResolver(root=tmp_path))
    workflow.reviewing = True
    context = ThreadContextStore(root=tmp_path / "context")

    routed, reply = _number_coordinator(tmp_path, workflow).process(
        channel_id="C1",
        thread_ts="1.1",
        text="<@U1> 2",
        thread_context=context,
        agent=TraceAgent(),
    )

    assert routed.intent is RequestIntent.PLAN_ANSWER and reply == "번호 기록"
    assert workflow.answered == PlanAnswer("choice", "2")


def test_a_bare_number_without_an_open_review_is_handled_as_usual(tmp_path: Path) -> None:
    workflow = _ReviewRecorder(project_resolver=ProjectResolver(root=tmp_path))
    context = ThreadContextStore(root=tmp_path / "context")

    routed, reply = _number_coordinator(tmp_path, workflow).process(
        channel_id="C1",
        thread_ts="1.1",
        text="2",
        thread_context=context,
        agent=TraceAgent(),
    )

    assert routed.intent is RequestIntent.PROJECT_ANALYSIS
    assert not hasattr(workflow, "answered") and reply != "번호 기록"

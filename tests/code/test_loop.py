"""The generic loop remains bounded and never performs side effects itself."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pytest import LogCaptureFixture

from src.code.loop import (
    AgentPlan,
    CodeAgentLoop,
    FinalStatus,
    PlannerFinal,
    PlanStep,
    ToolCall,
)
from src.code.tool_policy import ToolCategory
from src.code.tool_registry import ToolOutcome, ToolRegistry, definition
from src.core.agent_trace import AgentTraceRecorder


@dataclass
class SequencedPlanner:
    calls: list[ToolCall | PlannerFinal]

    def next_action(
        self, plan: AgentPlan, outcomes: list[ToolOutcome]
    ) -> ToolCall | PlannerFinal | None:
        del plan, outcomes
        return self.calls.pop(0) if self.calls else None


def _plan() -> AgentPlan:
    return AgentPlan(
        goal="구조 확인",
        selected_project="demo",
        steps=[PlanStep("구조 조회", ToolCall("list_files", {}))],
    )


def test_loop_collects_source_evidence_before_final() -> None:
    registry = ToolRegistry(
        [
            definition("list_files", ToolCategory.PROJECT_READ, lambda: ["src"]),
            definition(
                "read_file",
                ToolCategory.PROJECT_READ,
                lambda relative_path: "content",
                evidence_arg="relative_path",
            ),
        ]
    )
    planner = SequencedPlanner(
        [ToolCall("list_files", {}), ToolCall("read_file", {"relative_path": "src/app.py"})]
    )
    trace = AgentTraceRecorder("request-1", intent="code_work", selected_project="demo")

    answer = CodeAgentLoop(registry).run(_plan(), planner, trace=trace)

    assert answer.status is FinalStatus.COMPLETE
    assert answer.evidence.source_refs == ("src/app.py",)
    assert [step.tool_name for step in trace.steps] == ["list_files", "read_file"]


def test_trace_step_records_evidence_refs_for_a_successful_evidence_bearing_call() -> None:
    """A tool call that produces evidence must show up on the recorded
    `TraceStep.evidence_refs`, not just in the loop's own `sources` list —
    otherwise `agent_trace`'s logged `evidence_count` always reads 0 even
    when real evidence was collected, making the trace useless for
    diagnosing why a request ended up insufficient-evidence."""
    registry = ToolRegistry(
        [
            definition("list_files", ToolCategory.PROJECT_READ, lambda: ["src"]),
            definition(
                "read_file",
                ToolCategory.PROJECT_READ,
                lambda relative_path: "content",
                evidence_arg="relative_path",
            ),
        ]
    )
    planner = SequencedPlanner(
        [ToolCall("list_files", {}), ToolCall("read_file", {"relative_path": "src/app.py"})]
    )
    trace = AgentTraceRecorder("request-evidence-trace")

    CodeAgentLoop(registry).run(_plan(), planner, trace=trace)

    read_file_step = next(step for step in trace.steps if step.tool_name == "read_file")
    assert read_file_step.evidence_refs == ("src/app.py",)


def test_loop_collects_source_evidence_from_any_tool_named_differently() -> None:
    """The loop must not special-case the literal name `read_file`; evidence
    comes from `ToolOutcome.evidence`, whatever tool produced it."""
    registry = ToolRegistry(
        [
            definition(
                "load_source",
                ToolCategory.PROJECT_READ,
                lambda relative_path: "content",
                evidence_arg="relative_path",
            ),
        ]
    )
    planner = SequencedPlanner([ToolCall("load_source", {"relative_path": "src/app.py"})])

    answer = CodeAgentLoop(registry).run(_plan(), planner, trace=AgentTraceRecorder("request-ev"))

    assert answer.status is FinalStatus.COMPLETE
    assert answer.evidence.source_refs == ("src/app.py",)


def test_final_answer_uses_planner_supplied_summary_and_findings() -> None:
    registry = ToolRegistry(
        [
            definition(
                "read_file",
                ToolCategory.PROJECT_READ,
                lambda relative_path: "content",
                evidence_arg="relative_path",
            )
        ]
    )
    planner = SequencedPlanner(
        [
            ToolCall("read_file", {"relative_path": "src/app.py"}),
            PlannerFinal(
                summary="구현을 확인했습니다.",
                findings=("app.py는 진입점입니다.",),
                limitations=("테스트는 확인하지 않았습니다.",),
            ),
        ]
    )

    trace = AgentTraceRecorder("request-final")
    answer = CodeAgentLoop(registry).run(_plan(), planner, trace=trace)

    assert answer.status is FinalStatus.COMPLETE
    assert answer.summary == "구현을 확인했습니다."
    assert answer.findings == ("app.py는 진입점입니다.",)
    assert answer.limitations == ("테스트는 확인하지 않았습니다.",)


def test_loop_continues_after_policy_rejected_call() -> None:
    denied_calls: list[str] = []

    def dangerous() -> str:
        denied_calls.append("called")
        return "should not run"

    registry = ToolRegistry(
        [
            definition("dangerous", ToolCategory.BLOCKED, dangerous),
            definition(
                "read_file",
                ToolCategory.PROJECT_READ,
                lambda relative_path: "content",
                evidence_arg="relative_path",
            ),
        ]
    )
    planner = SequencedPlanner(
        [ToolCall("dangerous", {}), ToolCall("read_file", {"relative_path": "src/app.py"})]
    )

    trace = AgentTraceRecorder("request-policy")
    answer = CodeAgentLoop(registry).run(_plan(), planner, trace=trace)

    assert denied_calls == []
    assert answer.status is FinalStatus.COMPLETE
    assert answer.evidence.source_refs == ("src/app.py",)


def test_loop_continues_after_retryable_failure() -> None:
    registry = ToolRegistry(
        [
            definition(
                "read_file",
                ToolCategory.PROJECT_READ,
                lambda relative_path: "content",
                evidence_arg="relative_path",
            ),
        ]
    )
    planner = SequencedPlanner(
        [
            ToolCall("read_file", {"path": "wrong-arg-name"}),
            ToolCall("read_file", {"relative_path": "src/app.py"}),
        ]
    )
    trace = AgentTraceRecorder("request-retry")

    answer = CodeAgentLoop(registry).run(_plan(), planner, trace=trace)

    assert answer.status is FinalStatus.COMPLETE
    assert answer.evidence.source_refs == ("src/app.py",)


def test_loop_warns_once_on_duplicate_call_without_terminating() -> None:
    registry = ToolRegistry(
        [
            definition(
                "read_file",
                ToolCategory.PROJECT_READ,
                lambda relative_path: "content",
                evidence_arg="relative_path",
            )
        ]
    )
    planner = SequencedPlanner(
        [
            ToolCall("read_file", {"relative_path": "src/app.py"}),
            ToolCall("read_file", {"relative_path": "src/app.py"}),  # first repeat: warned only
        ]
    )

    answer = CodeAgentLoop(registry).run(_plan(), planner, trace=AgentTraceRecorder("request-warn"))

    assert answer.status is FinalStatus.COMPLETE
    assert answer.evidence.tools == ("read_file",)


def test_loop_stops_duplicate_call_and_does_not_loop_forever() -> None:
    registry = ToolRegistry(
        [
            definition(
                "read_file",
                ToolCategory.PROJECT_READ,
                lambda relative_path: "content",
                evidence_arg="relative_path",
            )
        ]
    )
    call = ToolCall("read_file", {"relative_path": "src/app.py"})
    # First call executes; the repeat is a warned duplicate; the second repeat blocks.
    planner = SequencedPlanner([call, call, call])

    answer = CodeAgentLoop(registry).run(_plan(), planner, trace=AgentTraceRecorder("request-2"))

    assert answer.status is FinalStatus.BLOCKED
    assert answer.evidence.tools == ("read_file",)


def test_loop_returns_confirmation_draft_without_running_write() -> None:
    writes: list[str] = []
    registry = ToolRegistry(
        [definition("write_file", ToolCategory.PROJECT_WRITE, lambda: writes.append("written"))]
    )
    planner = SequencedPlanner([ToolCall("write_file", {})])

    answer = CodeAgentLoop(registry).run(_plan(), planner, trace=AgentTraceRecorder("request-3"))

    assert answer.status is FinalStatus.AWAITING_CONFIRMATION
    assert answer.pending_call is not None
    assert writes == []


def test_trace_never_logs_raw_argument_or_result_content(caplog: LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    trace = AgentTraceRecorder("request-4")
    trace.record_tool(
        phase="act",
        tool_name="read_file",
        category="project_read",
        outcome="ok",
        args={"relative_path": "secret-value"},
        result="VERY_SECRET_FILE_CONTENT",
    )

    assert "secret-value" not in caplog.text
    assert "VERY_SECRET_FILE_CONTENT" not in caplog.text

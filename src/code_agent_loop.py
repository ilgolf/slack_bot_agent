"""Bounded plan → act → observe → replan loop for read-only agent work.

Writing and verification tools are intentionally returned as a pending action;
the Slack coordinator owns the later confirmed execution path.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from src.agent_trace import AgentTraceRecorder
from src.tool_policy import ConfirmationMode
from src.tool_registry import ToolOutcome, ToolRegistry


class FinalStatus(StrEnum):
    COMPLETE = "complete"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    BUDGET_EXHAUSTED = "budget_exhausted"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, object]


@dataclass(frozen=True)
class PlanStep:
    description: str
    tool_call: ToolCall


@dataclass(frozen=True)
class AgentPlan:
    goal: str
    selected_project: str
    steps: list[PlanStep]
    required_evidence: tuple[str, ...] = ("source",)


@dataclass(frozen=True)
class RunEvidence:
    source_refs: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class FinalAnswer:
    status: FinalStatus
    summary: str
    evidence: RunEvidence
    pending_call: ToolCall | None = None
    findings: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlannerFinal:
    """A planner's own conclusion, offered once it judges the collected
    evidence sufficient — as opposed to `None`, which just means "no more
    tool calls" and leaves the loop to render its generic completion text."""

    summary: str
    findings: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class NextActionPlanner(Protocol):
    def next_action(
        self, plan: AgentPlan, outcomes: list[ToolOutcome]
    ) -> ToolCall | PlannerFinal | None: ...


@dataclass
class CodeAgentLoop:
    registry: ToolRegistry
    max_steps: int = 16
    max_read_bytes: int = 128_000

    def run(
        self,
        plan: AgentPlan,
        planner: NextActionPlanner,
        *,
        trace: AgentTraceRecorder,
    ) -> FinalAnswer:
        outcomes: list[ToolOutcome] = []
        sources: list[str] = []
        tools: list[str] = []
        fingerprints: set[str] = set()
        warned_fingerprints: set[str] = set()
        read_bytes = 0

        for _ in range(self.max_steps):
            action = planner.next_action(plan, outcomes)
            if action is None or isinstance(action, PlannerFinal):
                if "source" in plan.required_evidence and not sources:
                    return FinalAnswer(
                        FinalStatus.INSUFFICIENT_EVIDENCE,
                        "근거 파일을 읽지 못해 완료할 수 없습니다.",
                        RunEvidence(tuple(sources), tuple(tools)),
                    )
                if action is None:
                    return FinalAnswer(
                        FinalStatus.COMPLETE,
                        "계획된 읽기 작업을 완료했습니다.",
                        RunEvidence(tuple(sources), tuple(tools)),
                    )
                return FinalAnswer(
                    FinalStatus.COMPLETE,
                    action.summary,
                    RunEvidence(tuple(sources), tuple(tools)),
                    findings=action.findings,
                    limitations=action.limitations,
                )
            call = action

            fingerprint = hashlib.sha256(
                f"{call.name}:{json.dumps(call.args, sort_keys=True, default=str)}".encode()
            ).hexdigest()
            if fingerprint in fingerprints:
                if fingerprint in warned_fingerprints:
                    trace.record_tool(
                        phase="policy",
                        tool_name=call.name,
                        category="unknown",
                        outcome="blocked",
                        args=call.args,
                    )
                    return FinalAnswer(
                        FinalStatus.BLOCKED,
                        "같은 도구 호출이 반복되어 중단했습니다.",
                        RunEvidence(tuple(sources), tuple(tools)),
                    )
                warned_fingerprints.add(fingerprint)
                trace.record_tool(
                    phase="policy",
                    tool_name=call.name,
                    category="unknown",
                    outcome="duplicate",
                    args=call.args,
                )
                outcomes.append(
                    ToolOutcome(
                        "blocked",
                        "같은 도구 호출이 이미 실행되었습니다. "
                        "현재 결과를 바탕으로 다른 행동을 선택하세요.",
                        retryable=True,
                    )
                )
                continue
            fingerprints.add(fingerprint)

            definition = self.registry.get(call.name)
            if definition is None:
                trace.record_tool(
                    phase="policy",
                    tool_name=call.name,
                    category="unknown",
                    outcome="blocked",
                    args=call.args,
                )
                outcomes.append(ToolOutcome("blocked", "등록되지 않은 도구입니다."))
                continue
            if definition.confirmation is ConfirmationMode.THREAD_CONFIRMATION:
                trace.record_tool(
                    phase="plan",
                    tool_name=call.name,
                    category=definition.category,
                    outcome="awaiting_confirmation",
                    args=call.args,
                )
                return FinalAnswer(
                    FinalStatus.AWAITING_CONFIRMATION,
                    "변경 또는 검증은 Slack 실행 확인이 필요합니다.",
                    RunEvidence(tuple(sources), tuple(tools)),
                    pending_call=call,
                )

            outcome = self.registry.invoke(call.name, call.args)
            trace.record_tool(
                phase="act",
                tool_name=call.name,
                category=definition.category,
                outcome=outcome.status,
                args=call.args,
                result=outcome.safe_message,
            )
            outcomes.append(outcome)
            tools.append(call.name)
            read_bytes += len(outcome.safe_message.encode("utf-8"))
            if outcome.status == "ok":
                sources.extend(outcome.evidence)
            if read_bytes > self.max_read_bytes:
                return FinalAnswer(
                    FinalStatus.BUDGET_EXHAUSTED,
                    "읽기 결과 예산을 초과해 중단했습니다.",
                    RunEvidence(tuple(sources), tuple(tools)),
                )

        return FinalAnswer(
            FinalStatus.BUDGET_EXHAUSTED,
            "도구 호출 예산을 초과해 중단했습니다.",
            RunEvidence(tuple(sources), tuple(tools)),
        )

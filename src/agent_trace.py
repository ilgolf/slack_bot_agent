"""Content-free trace records for one agent request.

The trace deliberately stores metadata only.  It is useful for diagnosing
tool-loop routing without persisting Slack text, prompts, source contents, or
external-service payloads.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field, replace
from threading import Lock
from time import monotonic

logger = logging.getLogger(__name__)


def _digest(value: object) -> str:
    text = str(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class TraceStep:
    phase: str
    tool_name: str
    category: str
    outcome: str
    duration_ms: int
    input_fields: tuple[str, ...] = ()
    input_size: int = 0
    result_size: int = 0
    evidence_refs: tuple[str, ...] = ()
    repair_attempts: int = 0
    termination_reason: str | None = None


@dataclass
class AgentTraceRecorder:
    """Collect a bounded, redacted request trace and emit safe log records."""

    request_id: str
    intent: str = "unknown"
    selected_project: str | None = None
    steps: list[TraceStep] = field(default_factory=list)

    def record_tool(
        self,
        *,
        phase: str,
        tool_name: str,
        category: str,
        outcome: str,
        args: dict[str, object] | None = None,
        result: object = "",
        evidence_refs: tuple[str, ...] = (),
        started_at: float | None = None,
    ) -> None:
        args = args or {}
        duration_ms = int((monotonic() - started_at) * 1000) if started_at else 0
        step = TraceStep(
            phase=phase,
            tool_name=tool_name,
            category=category,
            outcome=outcome,
            duration_ms=duration_ms,
            input_fields=tuple(sorted(args)),
            input_size=sum(len(str(value)) for value in args.values()),
            result_size=len(str(result)),
            evidence_refs=evidence_refs,
        )
        self.steps.append(step)
        logger.info(
            "agent_trace request_id=%s intent=%s project=%s phase=%s tool=%s "
            "category=%s outcome=%s duration_ms=%s fields=%s input_digest=%s "
            "result_digest=%s result_size=%s evidence_count=%s",
            self.request_id,
            self.intent,
            self.selected_project or "-",
            phase,
            tool_name,
            category,
            outcome,
            duration_ms,
            ",".join(step.input_fields) or "-",
            _digest(args),
            _digest(result),
            step.result_size,
            len(evidence_refs),
        )

    def summary(self) -> str:
        if not self.steps:
            return "실행된 도구가 없습니다."
        return "\n".join(
            f"- {step.tool_name}: {step.outcome} "
            f"({step.duration_ms}ms, 근거 {len(step.evidence_refs)}개)"
            + (
                f", 복구 {step.repair_attempts}회, 종료: {step.termination_reason}"
                if step.termination_reason is not None
                else ""
            )
            for step in self.steps
        )

    def record_code_work(self, *, repair_attempts: int, termination_reason: str) -> None:
        """Append only lifecycle metadata for a confirmed code-work run."""
        self.record_tool(
            phase="execution",
            tool_name="code_work",
            category="project_write",
            outcome="completed" if termination_reason == "verified" else "failed",
            args={"repair_attempts": repair_attempts, "termination_reason": termination_reason},
            result="",
        )
        self.steps[-1] = replace(
            self.steps[-1],
            repair_attempts=repair_attempts,
            termination_reason=termination_reason,
        )


class ThreadTraceStore:
    """Keeps the latest trace per Slack thread instead of a single mutable
    slot on the agent, so concurrent or sequential threads sharing one agent
    instance never see each other's trace."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._traces: dict[tuple[str, str], AgentTraceRecorder] = {}

    def put(self, channel_id: str, thread_ts: str, trace: AgentTraceRecorder) -> None:
        with self._lock:
            self._traces[(channel_id, thread_ts)] = trace

    def get(self, channel_id: str, thread_ts: str) -> AgentTraceRecorder | None:
        with self._lock:
            return self._traces.get((channel_id, thread_ts))

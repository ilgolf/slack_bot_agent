"""Read-only project analysis delegated to an external code agent (plan.md Phase 12).

`CodeAgentAnalysisAgent` owns project identification and result handling; an injected
`AgentRunner` owns the actual SDK call.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Collection
from dataclasses import dataclass, replace
from pathlib import Path
from time import monotonic
from typing import Protocol

from src.agent import AnalysisAgentError, AnalysisResult, insufficient_evidence_result
from src.agent_trace import AgentTraceRecorder, ThreadTraceStore
from src.code_plan_prompts import (
    build_code_plan_prompt,
    build_repair_prompt,
    parse_plan_response,
    parse_repair_response,
)
from src.execution_workflow import (
    AppliedSkill,
    CommandResult,
    ExecutionPlan,
    ExecutionStep,
    ExistingFile,
    ProjectContext,
)
from src.general_answer import build_general_answer_prompt
from src.project_resolver import ProjectResolver
from src.request_classifier import AnalysisRequest, RequestKind, classify_request

_PROMPT_TEMPLATE = (
    "당신은 로컬 프로젝트를 읽기 전용으로 분석하는 에이전트입니다.\n"
    "- 파일을 수정하지 마세요.\n"
    "- 답하기 전에 관련 소스 파일을 직접 읽고, 읽은 내용만 근거로 답하세요. "
    "일반 분석은 README 외 구현·설정·테스트 파일을 적어도 하나 직접 읽어야 합니다. "
    "README 요약 요청은 README.md만 읽으면 됩니다.\n\n"
    "질문: {question}\n\n"
    "한국어로, 핵심부터 간결한 마크다운으로 답변하세요."
)
_DEFAULT_TIMEOUT_SECONDS = 300.0
_DEFAULT_MAX_TURNS = 20
_DEFAULT_EDIT_TIMEOUT_SECONDS = 600.0
_DEFAULT_EDIT_MAX_TURNS = 40
_DEFAULT_EDIT_MAX_BUDGET_USD = 3.0


class RunnerError(Exception):
    """The runner failed; its message may hold SDK or CLI output and is never surfaced."""


class RunnerTimeout(Exception):
    """The runner exceeded its time budget."""


@dataclass(frozen=True)
class RunnerResult:
    text: str
    files_read: tuple[Path, ...] = ()


class AgentRunner(Protocol):
    name: str

    def run(
        self, prompt: str, *, cwd: Path, timeout_seconds: float, max_turns: int
    ) -> RunnerResult: ...

    def complete(self, prompt: str, *, timeout_seconds: float) -> str: ...


class CodeAgentAnalysisAgent:
    def __init__(
        self,
        *,
        runner: AgentRunner,
        project_resolver: ProjectResolver,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        max_turns: int = _DEFAULT_MAX_TURNS,
        thread_trace_store: ThreadTraceStore | None = None,
        edit_timeout_seconds: float = _DEFAULT_EDIT_TIMEOUT_SECONDS,
        edit_max_turns: int = _DEFAULT_EDIT_MAX_TURNS,
        edit_max_budget_usd: float = _DEFAULT_EDIT_MAX_BUDGET_USD,
        guidance: str = "",
    ) -> None:
        self.guidance = guidance
        self.edit_timeout_seconds = edit_timeout_seconds
        self.edit_max_turns = edit_max_turns
        self.edit_max_budget_usd = edit_max_budget_usd
        self.runner = runner
        self.project_resolver = project_resolver
        self.timeout_seconds = timeout_seconds
        self.max_turns = max_turns
        self.thread_trace_store = thread_trace_store or ThreadTraceStore()

    def analyze(
        self, question: str, *, channel_id: str = "-", thread_ts: str = "-"
    ) -> AnalysisResult:
        request = classify_request(question, self.project_resolver)
        trace = AgentTraceRecorder(
            request_id=uuid.uuid4().hex[:12],
            intent="project_analysis",
            selected_project=request.project_name,
        )
        self.thread_trace_store.put(channel_id, thread_ts, trace)
        if request.project_name is None:
            prompt = build_general_answer_prompt(
                request.current_message, request.thread_context, self.guidance
            )
            text = self._complete("answer", prompt, "-", channel_id, thread_ts)
            return _summary_from(text)
        project_path = self.project_resolver.resolve(request.project_name)
        started_at = monotonic()
        outcome = "error"
        result = AnalysisResult(summary="", findings=[])
        try:
            result = self._analyze_project(request, project_path)
            outcome = "ok" if result.sources else "insufficient_evidence"
        except RunnerTimeout:
            outcome = "timeout"
            result = AnalysisResult(
                summary="분석을 완료하지 못했습니다: 분석 시간이 초과되었습니다.",
                findings=[],
                limitations=["분석 시간 초과"],
            )
        except RunnerError:
            raise AnalysisAgentError("코드 에이전트 실행에 실패했습니다.") from None
        finally:
            trace.record_tool(
                phase="act",
                tool_name=self.runner.name,
                category="project_read",
                outcome=outcome,
                evidence_refs=tuple(result.sources),
                started_at=started_at,
            )
        return result

    def create_execution_plan(
        self,
        request: str,
        context: ProjectContext,
        skills: list[AppliedSkill],
        existing_files: list[ExistingFile],
        *,
        channel_id: str = "-",
        thread_ts: str = "-",
    ) -> ExecutionPlan:
        """Text-only: the runner gets no tools, so it can only propose a plan."""
        prompt = build_code_plan_prompt(request, context, skills, existing_files)
        text = self._complete("plan", prompt, context.project_name, channel_id, thread_ts)
        return parse_plan_response(text)

    def create_repair_steps(
        self,
        plan: ExecutionPlan,
        existing_files: list[ExistingFile],
        checks: list[CommandResult],
        *,
        channel_id: str = "-",
        thread_ts: str = "-",
    ) -> list[ExecutionStep]:
        prompt = build_repair_prompt(plan, existing_files, checks)
        text = self._complete("repair", prompt, plan.project_name, channel_id, thread_ts)
        return parse_repair_response(text)

    @property
    def supports_edit(self) -> bool:
        return callable(getattr(self.runner, "edit", None))

    def edit_code(
        self,
        prompt: str,
        worktree: Path,
        *,
        project_name: str,
        named_paths: frozenset[str] = frozenset(),
        write_roots: Collection[str] | None = None,
        channel_id: str = "-",
        thread_ts: str = "-",
    ) -> str:
        """Let the runner edit files inside `worktree`; returns the agent's final text.
        `write_roots`, when given, limit the edit to those directories."""
        edit = getattr(self.runner, "edit", None)
        if not callable(edit):
            raise AnalysisAgentError("이 코드 에이전트는 파일 편집을 지원하지 않습니다.")
        limits = {} if write_roots is None else {"write_roots": write_roots}
        return self._traced(
            "edit",
            project_name,
            channel_id,
            thread_ts,
            lambda: (
                edit(
                    prompt,
                    cwd=worktree,
                    timeout_seconds=self.edit_timeout_seconds,
                    max_turns=self.edit_max_turns,
                    max_budget_usd=self.edit_max_budget_usd,
                    named_paths=named_paths,
                    **limits,
                ).text
            ),
        )

    def _complete(
        self, phase: str, prompt: str, project_name: str, channel_id: str, thread_ts: str
    ) -> str:
        return self._traced(
            phase,
            project_name,
            channel_id,
            thread_ts,
            lambda: self.runner.complete(prompt, timeout_seconds=self.timeout_seconds),
        )

    def _traced(
        self,
        phase: str,
        project_name: str,
        channel_id: str,
        thread_ts: str,
        call: Callable[[], str],
    ) -> str:
        """One runner call. Runner failures never surface their message; the trace step
        carries only the runner name and outcome."""
        trace = self._trace_for(channel_id, thread_ts, project_name, phase)
        started_at = monotonic()
        outcome = "error"
        try:
            text = call()
            outcome = "ok"
            return text
        except RunnerTimeout:
            outcome = "timeout"
            raise AnalysisAgentError("코드 에이전트 응답 시간이 초과되었습니다.") from None
        except RunnerError:
            raise AnalysisAgentError("코드 에이전트 실행에 실패했습니다.") from None
        finally:
            trace.record_tool(
                phase=phase,
                tool_name=self.runner.name,
                category="project_write" if phase == "edit" else "project_read",
                outcome=outcome,
                started_at=started_at,
            )

    def _trace_for(
        self, channel_id: str, thread_ts: str, project_name: str, phase: str
    ) -> AgentTraceRecorder:
        trace = self.thread_trace_store.get(channel_id, thread_ts)
        if trace is None:
            trace = AgentTraceRecorder(
                request_id=uuid.uuid4().hex[:12],
                intent="code_edit" if phase == "edit" else "code_plan",
                selected_project=project_name,
            )
            self.thread_trace_store.put(channel_id, thread_ts, trace)
        return trace

    def _analyze_project(self, request: AnalysisRequest, project_path: Path) -> AnalysisResult:
        result = self._run(_PROMPT_TEMPLATE.format(question=request.question), project_path)
        sources = _project_relative_sources(project_path, result.files_read)
        if not _is_sufficient(request.kind, sources):
            result = self._run(
                _with_retry_instruction(_PROMPT_TEMPLATE.format(question=request.question)),
                project_path,
            )
            sources = _project_relative_sources(project_path, result.files_read)
        if not _is_sufficient(request.kind, sources):
            return insufficient_evidence_result()
        return replace(_summary_from(result.text), sources=sources)

    def _run(self, prompt: str, project_path: Path) -> RunnerResult:
        return self.runner.run(
            prompt,
            cwd=project_path,
            timeout_seconds=self.timeout_seconds,
            max_turns=self.max_turns,
        )


def _with_retry_instruction(prompt: str) -> str:
    return f"{prompt}\n\n소스 파일을 찾아 직접 읽은 뒤 그 내용을 근거로 답하세요."


def _summary_from(text: str) -> AnalysisResult:
    summary = text.strip()
    if not summary:
        raise AnalysisAgentError("코드 에이전트가 빈 응답을 반환했습니다.")
    return AnalysisResult(summary=summary, findings=[])


def _project_relative_sources(project_path: Path, files_read: tuple[Path, ...]) -> list[str]:
    root = project_path.resolve()
    resolved = (path.resolve() for path in files_read)
    return [path.relative_to(root).as_posix() for path in resolved if path.is_relative_to(root)]


def _is_sufficient(kind: RequestKind, sources: list[str]) -> bool:
    """A README summary may rest on README.md alone; any other analysis needs at
    least one non-README source."""
    if kind is RequestKind.README_SUMMARY:
        return bool(sources)
    return any(Path(source).name.casefold() != "readme.md" for source in sources)

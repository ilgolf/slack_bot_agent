"""Read-only project analysis delegated to an external code agent (plan.md Phase 12).

`CodeAgentAnalysisAgent` owns project identification and result handling; an injected
`AgentRunner` owns the actual SDK call.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from time import monotonic
from typing import Protocol

from src.agent import AnalysisAgentError, AnalysisResult, insufficient_evidence_result
from src.agent_trace import AgentTraceRecorder, ThreadTraceStore
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


class CodeAgentAnalysisAgent:
    def __init__(
        self,
        *,
        runner: AgentRunner,
        project_resolver: ProjectResolver,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        max_turns: int = _DEFAULT_MAX_TURNS,
        thread_trace_store: ThreadTraceStore | None = None,
    ) -> None:
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
            return AnalysisResult(summary="분석할 프로젝트명을 알려주세요.", findings=[])
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

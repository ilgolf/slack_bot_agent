"""LangChainAnalysisAgent: `analyze(question)` contract, backed by a LangChain chat
model with tool-calling access to a `ProjectResolver`. Project selection happens
through tool-calling (`list_projects`, and project-scoped `read_file`/`list_files`
that resolve a project name themselves) rather than a pre-resolved project path — see
plan.md Section 8.
"""

from __future__ import annotations

import inspect
import json
import logging
import re
import uuid
from collections.abc import Callable
from pathlib import Path
from time import monotonic
from typing import Any, Protocol

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.tools import StructuredTool

from src.agent import (
    AnalysisAgentError,
    AnalysisResult,
    insufficient_evidence_result,
)
from src.agent_trace import AgentTraceRecorder, ThreadTraceStore
from src.artifact_generation import ArtifactDraft, ArtifactDraftCreator
from src.code_agent_loop import AgentPlan, CodeAgentLoop, FinalAnswer, FinalStatus
from src.code_agent_planner import LangChainNextActionPlanner
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
from src.message_text import content_text
from src.project_resolver import ProjectResolver
from src.request_classifier import AnalysisRequest, RequestKind, classify_request
from src.tool_policy import ToolCategory
from src.tool_registry import ToolRegistry, definition
from src.tools import read_file

_PROMPT_TEMPLATE = (
    "당신은 로컬 프로젝트를 분석하는 에이전트입니다.\n"
    "- 사용자가 특정 프로젝트의 README.md 요약을 요청하면, 그 프로젝트의 README.md만 "
    "read_file로 한 번 읽고 즉시 최종 JSON을 반환하세요. "
    "다른 파일이나 디렉터리를 탐색하지 마세요.\n"
    "- 명시되거나 스레드 맥락에서 식별되는 프로젝트만 조사하세요. 여러 프로젝트 조사를 요청받지 "
    "않았다면 다른 프로젝트를 탐색하지 마세요.\n"
    "- 프로젝트를 식별할 수 없으면 도구를 호출하지 말고, "
    "프로젝트명을 요청하는 최종 JSON을 반환하세요.\n"
    "- 도구 결과만 근거로 답하고, 충분한 결과를 얻은 뒤에는 "
    "추가 도구 호출 없이 최종 JSON을 반환하세요.\n\n"
    "- 일반 분석에서는 먼저 선택된 프로젝트의 루트에 list_files를 호출해 구조를 확인하세요. "
    "README.md는 사용자가 README 요약을 요청한 경우에만 단독 근거가 될 수 있습니다. "
    "일반 분석은 README 외 구현·설정·테스트 파일을 적어도 하나 read_file로 읽은 뒤 답하세요.\n"
    "- 루트에 모듈 디렉터리만 보이는 멀티 모듈 프로젝트처럼 구조가 깊으면 list_files를 "
    "반복하지 말고 find_files로 소스 파일을 찾은 뒤 read_file로 읽으세요. "
    "find_files 결과에 '결과가 잘렸습니다'가 있으면 relative_path를 관련 모듈 디렉터리로 "
    "좁혀 다시 호출하세요.\n\n"
    "질문: {question}\n\n"
    "코드 블록이나 설명을 덧붙이지 말고 다음 JSON 형식으로만 답변하세요: "
    '{{"summary": "...", "findings": ["..."], "limitations": ["..."]}}'
)

_DEFAULT_MAX_TOOL_ITERATIONS = 16

ProjectTool = Callable[..., Any]
logger = logging.getLogger(__name__)


def _new_request_id() -> str:
    """One agent instance is shared across every Slack thread, so each call
    needs its own id — a fixed literal would make concurrent/sequential
    requests indistinguishable in trace logs."""
    return uuid.uuid4().hex


def _parse_analysis_result(content: object, *, sources: list[str] | None = None) -> AnalysisResult:
    """Parse the model's JSON response, accepting an otherwise-valid fenced block."""
    text = content_text(content).strip()
    fenced_json = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, flags=re.DOTALL)
    if fenced_json:
        text = fenced_json.group(1)
    text = text.replace("\u00a0", " ")

    try:
        data = json.loads(text)
        return AnalysisResult(
            summary=data["summary"],
            findings=data["findings"],
            sources=sources or [],
            limitations=data.get("limitations", []),
        )
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise AnalysisAgentError(
            f"model reply is not the expected JSON shape: {content!r}"
        ) from exc


class ChatModel(Protocol):
    """The slice of a LangChain chat model's interface this agent needs — lets a
    hand-rolled fake stand in for a real `BaseChatModel` in tests."""

    def invoke(self, input: list[BaseMessage]) -> Any: ...


_ANALYSIS_TOOL_NAMES = {"read_file", "list_files", "find_files"}


def _wrap_project_bound_tool(
    func: ProjectTool, project_resolver: ProjectResolver, project_name: str
) -> Callable[..., Any]:
    """Binds both `project_resolver` and the already-selected `project_name`
    via a real closure (not `functools.partial`, which breaks
    `StructuredTool.from_function`'s schema introspection). The schema shown
    to the model has no `project_name` field at all, so the model cannot even
    express analyzing a different project — a stronger guarantee than
    validating the argument at call time.
    """
    extra_params = list(inspect.signature(func).parameters.values())[2:]

    if not extra_params:

        def wrapper_no_args() -> Any:
            return func(project_resolver, project_name)

        return wrapper_no_args

    if extra_params[0].default is inspect.Parameter.empty:

        def wrapper_one_arg_required(relative_path: str) -> Any:
            return func(project_resolver, project_name, relative_path)

        return wrapper_one_arg_required

    def wrapper_one_arg_optional(relative_path: str = ".") -> Any:
        return func(project_resolver, project_name, relative_path)

    return wrapper_one_arg_optional


class LangChainAnalysisAgent(ArtifactDraftCreator):
    def __init__(
        self,
        *,
        chat_model: ChatModel,
        project_resolver: ProjectResolver,
        planning_model: ChatModel | None = None,
        tools: list[ProjectTool] | None = None,
        max_tool_iterations: int = _DEFAULT_MAX_TOOL_ITERATIONS,
        thread_trace_store: ThreadTraceStore | None = None,
    ) -> None:
        if max_tool_iterations < 1:
            raise ValueError("max_tool_iterations must be positive")
        self.chat_model = chat_model
        self.planning_model = planning_model or chat_model
        self.project_resolver = project_resolver
        self.tool_funcs = tools or []
        self.max_tool_iterations = max_tool_iterations
        self.thread_trace_store = thread_trace_store or ThreadTraceStore()

    def _build_tools(self, project_name: str) -> list[StructuredTool]:
        """Only `read_file`/`list_files`, bound to `project_name` — the model
        is never given `list_projects` or a `project_name` field to fill in,
        since the project for this request is already decided."""
        built_tools = []
        for func in self.tool_funcs:
            if func.__name__ not in _ANALYSIS_TOOL_NAMES:
                continue
            wrapper = _wrap_project_bound_tool(func, self.project_resolver, project_name)
            built_tools.append(
                StructuredTool.from_function(
                    func=wrapper,
                    name=func.__name__,
                    description=func.__doc__ or func.__name__,
                )
            )
        return built_tools

    def _build_registry(self, project_name: str) -> ToolRegistry:
        """Same bound functions as `_build_tools`, but wired through the
        shared `ToolRegistry` so execution goes through one policy-normalizing
        path instead of a second, ad hoc `StructuredTool.invoke` call site."""
        return ToolRegistry(
            [
                definition(
                    func.__name__,
                    ToolCategory.PROJECT_READ,
                    _wrap_project_bound_tool(func, self.project_resolver, project_name),
                    evidence_arg="relative_path" if func.__name__ == "read_file" else None,
                )
                for func in self.tool_funcs
                if func.__name__ in _ANALYSIS_TOOL_NAMES
            ]
        )

    def _build_planner(self, project_name: str) -> LangChainNextActionPlanner:
        """Bind the same tools `_build_tools` exposes to the LLM, then hand
        the bound model to a fresh `LangChainNextActionPlanner` for one
        `CodeAgentLoop` run — a planner is stateful and single-use."""
        tools = self._build_tools(project_name)
        chat_model: Any = self.chat_model
        if tools and hasattr(chat_model, "bind_tools"):
            chat_model = chat_model.bind_tools(tools)
        return LangChainNextActionPlanner(chat_model=chat_model)

    def analyze(
        self, question: str, *, channel_id: str = "-", thread_ts: str = "-"
    ) -> AnalysisResult:
        request = classify_request(question, self.project_resolver)
        trace = AgentTraceRecorder(
            request_id=_new_request_id(),
            intent="project_analysis",
            selected_project=request.project_name,
        )
        self.thread_trace_store.put(channel_id, thread_ts, trace)
        if request.kind in {RequestKind.README_SUMMARY, RequestKind.FILE_SUMMARY}:
            return self._summarize_file(request, channel_id=channel_id, thread_ts=thread_ts)
        if request.project_name is None:
            return AnalysisResult(
                summary="분석할 프로젝트명을 알려주세요.",
                findings=[],
            )

        registry = self._build_registry(request.project_name)
        planner = self._build_planner(request.project_name)
        plan = AgentPlan(
            goal=_PROMPT_TEMPLATE.format(question=question),
            selected_project=request.project_name,
            steps=[],
            required_evidence=("source",) if self.tool_funcs else (),
        )
        answer = CodeAgentLoop(registry, max_steps=self.max_tool_iterations).run(
            plan, planner, trace=trace
        )
        return self._map_final_answer(answer)

    @staticmethod
    def _map_final_answer(answer: FinalAnswer) -> AnalysisResult:
        sources = list(answer.evidence.source_refs)
        if answer.status is FinalStatus.COMPLETE:
            has_non_readme_source = any(
                Path(source).name.casefold() != "readme.md" for source in sources
            )
            if sources and not has_non_readme_source:
                # every source read so far is README.md — not enough grounds
                # for a general analysis answer, same as no source at all.
                return insufficient_evidence_result()
            return AnalysisResult(
                summary=answer.summary,
                findings=list(answer.findings),
                sources=sources,
                limitations=list(answer.limitations),
            )
        if answer.status is FinalStatus.INSUFFICIENT_EVIDENCE:
            return insufficient_evidence_result()

        executed_tools = ", ".join(answer.evidence.tools) or "없음"
        return AnalysisResult(
            summary=f"분석을 완료하지 못했습니다: {answer.summary}",
            findings=[],
            sources=sources,
            limitations=[f"실행한 도구: {executed_tools}"],
        )

    def _summarize_file(
        self, request: AnalysisRequest, *, channel_id: str, thread_ts: str
    ) -> AnalysisResult:
        if request.project_name is None:
            return AnalysisResult(
                summary="어떤 프로젝트의 파일을 요약할지 프로젝트명을 알려주세요.",
                findings=[],
            )
        if request.relative_path is None:
            raise AnalysisAgentError("file summary request is missing a relative path")

        logger.info(
            "plan_selected kind=%s project=%s path=%s",
            request.kind,
            request.project_name,
            request.relative_path,
        )
        logger.info("tool_call_started name=read_file fields=project_name,relative_path")
        started_at = monotonic()
        file_content = read_file(self.project_resolver, request.project_name, request.relative_path)
        logger.info(
            "tool_call_completed name=read_file result_type=%s", type(file_content).__name__
        )
        trace = AgentTraceRecorder(
            request_id=_new_request_id(),
            intent="file_summary",
            selected_project=request.project_name,
        )
        trace.record_tool(
            phase="act",
            tool_name="read_file",
            category="project_read",
            outcome="failed" if _is_tool_error(file_content) else "ok",
            args={"project_name": request.project_name, "relative_path": request.relative_path},
            result=file_content,
            evidence_refs=(request.relative_path,),
            started_at=started_at,
        )
        self.thread_trace_store.put(channel_id, thread_ts, trace)
        if _is_tool_error(file_content):
            return AnalysisResult(
                summary=file_content,
                findings=[],
                limitations=["파일을 읽을 수 없습니다."],
            )

        prompt = (
            f"프로젝트: {request.project_name}\n"
            f"다음 {request.relative_path}만 근거로 한국어로 요약하세요. "
            "다른 도구를 호출하거나 추측하지 마세요.\n\n"
            f"파일 내용:\n{file_content}\n\n"
            "코드 블록 없이 JSON만 반환하세요: "
            '{"summary": "...", "findings": ["..."], "limitations": ["..."]}'
        )
        response = self.chat_model.invoke([HumanMessage(content=prompt)])
        return _parse_analysis_result(response.content, sources=[request.relative_path])

    def create_artifact_draft(self, thread_context: str, destination: Path) -> ArtifactDraft:
        """Ask the LLM to shape a file draft from explicit thread-context facts."""
        is_table = destination.suffix.casefold() in {".csv", ".xlsx"}
        required_shape = (
            '{"kind": "table", "headers": ["..."], "rows": [{"header": "value"}]}'
            if is_table
            else '{"kind": "text", "content": "..."}'
        )
        prompt = (
            f"사용자가 요청한 출력 파일은 {destination.name}입니다. 다음 Slack 스레드 맥락만 "
            "근거로 파일 초안을 만드세요. 없는 사실·이메일·코드·값을 추측하지 마세요. "
            f"코드 블록 없이 JSON만 반환하세요: {required_shape}\n\n"
            f"스레드 맥락:\n{thread_context}"
        )
        response = self.chat_model.invoke([HumanMessage(content=prompt)])
        text = content_text(response.content).strip()
        fenced_json = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, flags=re.DOTALL)
        if fenced_json:
            text = fenced_json.group(1)
        try:
            payload = json.loads(text.replace("\u00a0", " "))
            kind = payload["kind"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise AnalysisAgentError("model reply is not a valid artifact draft") from exc

        if kind == "text" and isinstance(payload.get("content"), str):
            return ArtifactDraft(destination=destination, kind="text", content=payload["content"])
        headers = payload.get("headers")
        rows = payload.get("rows")
        if kind == "table" and isinstance(headers, list) and isinstance(rows, list):
            if not all(isinstance(header, str) for header in headers) or not all(
                isinstance(row, dict) for row in rows
            ):
                raise AnalysisAgentError("table draft has an invalid shape")
            normalized_rows = [
                {str(key): str(value) for key, value in row.items() if value is not None}
                for row in rows
            ]
            return ArtifactDraft(
                destination=destination,
                kind="table",
                headers=headers,
                rows=normalized_rows,
            )
        raise AnalysisAgentError("artifact draft does not match the requested file type")

    def create_execution_plan(
        self,
        request: str,
        context: ProjectContext,
        skills: list[AppliedSkill],
        existing_files: list[ExistingFile],
    ) -> ExecutionPlan:
        """Return a bounded ExecutionPlan from project evidence.

        Raises PlanResponseFormatError when the model does not return the expected plan.
        The workflow owns retry and approval decisions.
        """
        prompt = build_code_plan_prompt(request, context, skills, existing_files)
        response = self.planning_model.invoke([HumanMessage(content=prompt)])
        return parse_plan_response(response.content)

    def create_repair_steps(
        self,
        plan: ExecutionPlan,
        existing_files: list[ExistingFile],
        checks: list[CommandResult],
    ) -> list[ExecutionStep]:
        """Propose one bounded repair using only approved files and failed checks."""
        prompt = build_repair_prompt(plan, existing_files, checks)
        response = self.chat_model.invoke([HumanMessage(content=prompt)])
        return parse_repair_response(response.content)


def _is_tool_error(result: str) -> bool:
    return result.startswith(
        ("프로젝트를 찾을 수 없습니다", "파일을 읽을 수 없습니다", "텍스트 파일", "허용되지 않은")
    )

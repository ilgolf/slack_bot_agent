"""Code analysis agent boundary.

`FakeAnalysisAgent` stands in for the real LLM-backed agent; `get_agent` selects
between it and a real `LangChainAnalysisAgent`, mirroring v1's `get_agent` seam.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from src.config import Settings
from src.harness import load_guidance
from src.project_resolver import ProjectResolver
from src.tools import find_files, list_files, list_projects, read_file

if TYPE_CHECKING:
    from src.code_agent_analysis import CodeAgentAnalysisAgent
    from src.langchain_agent import LangChainAnalysisAgent


class AnalysisAgentError(Exception):
    """Raised when an analysis agent fails — never a raw parsing/runtime exception
    from a specific agent implementation, so callers (e.g. `dispatch_command`) can
    catch one error type regardless of which agent raised it."""


class PlanResponseFormatError(AnalysisAgentError):
    """The model response could not be parsed as a bounded execution plan."""


@dataclass(frozen=True)
class AnalysisResult:
    summary: str
    findings: list[str]
    sources: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


def insufficient_evidence_result() -> AnalysisResult:
    return AnalysisResult(
        summary="분석 근거 파일을 읽지 못했습니다. 분석할 파일을 지정해 주세요.",
        findings=[],
        limitations=["근거 파일 없이 분석 결과를 만들 수 없습니다."],
    )


class AnalysisAgent(Protocol):
    def analyze(
        self, question: str, *, channel_id: str = "-", thread_ts: str = "-"
    ) -> AnalysisResult: ...


class FakeAnalysisAgent:
    def analyze(
        self, question: str, *, channel_id: str = "-", thread_ts: str = "-"
    ) -> AnalysisResult:
        del channel_id, thread_ts
        return AnalysisResult(
            summary=f"'{question}'에 대한 가짜 분석 결과입니다.",
            findings=["가짜 finding 1", "가짜 finding 2"],
        )


def build_chat_model(provider: str, *, settings: Settings) -> Any:
    """The LangChain chat model for an API-key provider (`anthropic` or `openai`)."""
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        # langchain-anthropic's pydantic model accepts `model`/`api_key` as aliases
        # and coerces a plain `str` into `SecretStr` at runtime; its generated stub
        # doesn't reflect either, hence the ignores.
        return ChatAnthropic(
            api_key=settings.anthropic_api_key,  # type: ignore[arg-type]
            model=settings.llm_model or "claude-3-5-sonnet-latest",  # type: ignore[call-arg]
        )
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            api_key=settings.openai_api_key,  # type: ignore[arg-type]
            model=settings.llm_model or "gpt-4o-mini",
        )
    raise ValueError(f"unknown LLM provider: {provider!r}")


def get_agent(
    provider: str, *, settings: Settings
) -> AnalysisAgent | LangChainAnalysisAgent | CodeAgentAnalysisAgent:
    if provider == "fake":
        return FakeAnalysisAgent()

    project_resolver = ProjectResolver(root=Path(settings.projects_root).expanduser())

    if provider in {"anthropic", "openai"}:
        from src.langchain_agent import LangChainAnalysisAgent

        return LangChainAnalysisAgent(
            chat_model=build_chat_model(provider, settings=settings),
            project_resolver=project_resolver,
            tools=[list_projects, read_file, list_files, find_files],
            max_tool_iterations=settings.agent_max_tool_iterations,
        )

    if provider in {"claude_code", "codex"}:
        from src.agent_runners import create_claude_runner, create_codex_runner
        from src.code_agent_analysis import CodeAgentAnalysisAgent

        runner = create_claude_runner() if provider == "claude_code" else create_codex_runner()
        return CodeAgentAnalysisAgent(
            runner=runner,
            project_resolver=project_resolver,
            timeout_seconds=settings.agent_runner_timeout_seconds,
            max_turns=settings.agent_runner_max_turns,
            edit_timeout_seconds=settings.agent_edit_timeout_seconds,
            edit_max_turns=settings.agent_edit_max_turns,
            edit_max_budget_usd=settings.agent_edit_max_budget_usd,
            guidance=load_guidance(),
        )

    raise ValueError(f"unknown LLM provider: {provider!r}")

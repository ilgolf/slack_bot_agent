"""Code analysis agent boundary.

`FakeAnalysisAgent` stands in for the real LLM-backed agent; `get_agent` selects
between it and a real `LangChainAnalysisAgent`, mirroring v1's `get_agent` seam.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from src.config import Settings
from src.project_resolver import ProjectResolver
from src.tools import list_files, list_projects, read_file

if TYPE_CHECKING:
    from src.langchain_agent import LangChainAnalysisAgent


class AnalysisAgentError(Exception):
    """Raised when an analysis agent fails — never a raw parsing/runtime exception
    from a specific agent implementation, so callers (e.g. `dispatch_command`) can
    catch one error type regardless of which agent raised it."""


@dataclass(frozen=True)
class AnalysisResult:
    summary: str
    findings: list[str]
    sources: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


class AnalysisAgent(Protocol):
    def analyze(self, question: str) -> AnalysisResult: ...


class FakeAnalysisAgent:
    def analyze(self, question: str) -> AnalysisResult:
        return AnalysisResult(
            summary=f"'{question}'에 대한 가짜 분석 결과입니다.",
            findings=["가짜 finding 1", "가짜 finding 2"],
        )


def get_agent(provider: str, *, settings: Settings) -> AnalysisAgent | LangChainAnalysisAgent:
    if provider == "fake":
        return FakeAnalysisAgent()

    project_resolver = ProjectResolver(root=Path(settings.projects_root).expanduser())

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        from src.langchain_agent import LangChainAnalysisAgent

        # langchain-anthropic's pydantic model accepts `model`/`api_key` as aliases
        # and coerces a plain `str` into `SecretStr` at runtime; its generated stub
        # doesn't reflect either, hence the ignores.
        anthropic_chat_model = ChatAnthropic(
            api_key=settings.anthropic_api_key,  # type: ignore[arg-type]
            model="claude-3-5-sonnet-latest",  # type: ignore[call-arg]
        )
        return LangChainAnalysisAgent(
            chat_model=anthropic_chat_model,
            project_resolver=project_resolver,
            tools=[list_projects, read_file, list_files],
        )

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        from src.langchain_agent import LangChainAnalysisAgent

        openai_chat_model = ChatOpenAI(
            api_key=settings.openai_api_key,  # type: ignore[arg-type]
            model="gpt-4o-mini",
        )
        return LangChainAnalysisAgent(
            chat_model=openai_chat_model,
            project_resolver=project_resolver,
            tools=[list_projects, read_file, list_files],
        )

    raise ValueError(f"unknown LLM provider: {provider!r}")

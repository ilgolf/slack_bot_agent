"""Deterministic classification for narrow local-project analysis requests."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from src.core.project_resolver import ProjectResolver


class RequestKind(StrEnum):
    README_SUMMARY = "readme_summary"
    FILE_SUMMARY = "file_summary"
    GENERAL_ANALYSIS = "general_analysis"


_EXPLICIT_PROJECT = re.compile(
    r"(?<![\w./-])([\w.-]+)\s+(?:프로젝트|project)(?=$|[에은는을를의]|\W)", re.I
)
_DEICTIC_PROJECTS = frozenset({"이", "그", "저", "현재", "해당", "우리", "this", "the"})


@dataclass(frozen=True)
class AnalysisRequest:
    """A user request with the project inferred from its current thread context."""

    question: str
    current_message: str
    thread_context: str
    project_name: str | None
    kind: RequestKind
    relative_path: str | None = None


def classify_request(question: str, project_resolver: ProjectResolver) -> AnalysisRequest:
    """Classify a narrow request without asking the LLM to search every project."""
    thread_context, current_message = _split_thread_context(question)
    project_names = sorted(
        (path.name for path in project_resolver.project_directories()), key=len, reverse=True
    )
    project_name = _explicit_project_name(current_message) or _find_project_name(
        current_message, project_names
    )
    if project_name is None:
        project_name = _find_project_name(question, project_names)
    normalized_question = current_message.casefold()
    if "readme" in normalized_question and _is_summary_request(normalized_question):
        return AnalysisRequest(
            question=question,
            current_message=current_message,
            thread_context=thread_context,
            project_name=project_name,
            kind=RequestKind.README_SUMMARY,
            relative_path="README.md",
        )

    relative_path = _extract_file_path(current_message)
    if relative_path and _is_summary_request(normalized_question):
        return AnalysisRequest(
            question=question,
            current_message=current_message,
            thread_context=thread_context,
            project_name=project_name,
            kind=RequestKind.FILE_SUMMARY,
            relative_path=relative_path,
        )

    return AnalysisRequest(
        question=question,
        current_message=current_message,
        thread_context=thread_context,
        project_name=project_name,
        kind=RequestKind.GENERAL_ANALYSIS,
    )


def _find_project_name(text: str, project_names: list[str]) -> str | None:
    normalized_text = text.casefold()
    return next((name for name in project_names if name.casefold() in normalized_text), None)


def find_project_name_candidates(text: str, project_resolver: ProjectResolver) -> list[str]:
    """All distinct configured project names mentioned anywhere in `text`.

    Used to detect when a request relying on thread history — rather than an
    explicit name in the current message — can't be resolved to one project
    without guessing.
    """
    project_names = sorted(
        (path.name for path in project_resolver.project_directories()), key=len, reverse=True
    )
    normalized_text = text.casefold()
    return [name for name in project_names if name.casefold() in normalized_text]


def _explicit_project_name(text: str) -> str | None:
    match = _EXPLICIT_PROJECT.search(text)
    if match is None or match.group(1).casefold() in _DEICTIC_PROJECTS:
        return None
    return match.group(1)


def _split_thread_context(question: str) -> tuple[str, str]:
    marker = "현재 요청:\n"
    if marker not in question:
        return "", question
    thread_context, current_message = question.rsplit(marker, maxsplit=1)
    return thread_context.removeprefix("스레드 맥락:\n").rstrip(), current_message


def _is_summary_request(normalized_question: str) -> bool:
    return any(marker in normalized_question for marker in ("요약", "summary", "summarize"))


def _extract_file_path(text: str) -> str | None:
    match = re.search(r"(?<!\S)([\w./-]+\.[A-Za-z0-9]+)(?!\S)", text)
    return match.group(1) if match else None

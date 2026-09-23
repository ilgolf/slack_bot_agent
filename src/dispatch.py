"""Command dispatch: forwards a Slack thread message straight to the analysis
agent — no fixed-grammar gate — recording thread context and translating an
`AnalysisAgentError` into a clear reply instead of letting it propagate (see
plan.md Section 8: project selection now happens through the agent's own
tool-calling, not a pre-dispatch `ProjectResolver` lookup).
"""

from __future__ import annotations

import logging

from src.agent import AnalysisAgent, AnalysisAgentError, AnalysisResult
from src.system_inquiry import answer_system_inquiry
from src.thread_context import ThreadContextStore

logger = logging.getLogger(__name__)


def dispatch_command(
    channel_id: str,
    thread_ts: str,
    text: str,
    *,
    thread_context: ThreadContextStore,
    agent: AnalysisAgent,
) -> str:
    if not text.strip():
        return ""

    system_response = answer_system_inquiry(text)
    if system_response is not None:
        thread_context.append(channel_id, thread_ts, text)
        thread_context.append(channel_id, thread_ts, system_response)
        logger.info("system_inquiry_completed kind=external_integration")
        return system_response

    prior_context = thread_context.read(channel_id, thread_ts)
    thread_context.append(channel_id, thread_ts, text)
    question = text
    if prior_context:
        question = f"스레드 맥락:\n{prior_context}\n현재 요청:\n{text}"

    try:
        result = agent.analyze(question)
    except AnalysisAgentError as exc:
        response = f"❌ 작업 실패\n- {exc}"
        thread_context.append(channel_id, thread_ts, response)
        logger.warning("analysis_failed reason=%s", exc)
        return response

    response = render_result(result)
    thread_context.append(channel_id, thread_ts, response)
    logger.info("analysis_completed sources=%s", result.sources)
    return response


def render_result(result: AnalysisResult) -> str:
    """Render a structured analysis result for a readable Slack thread reply."""
    sections = [result.summary]
    if result.findings:
        sections.append("핵심 발견:\n" + "\n".join(f"- {finding}" for finding in result.findings))
    if result.sources:
        sections.append("근거 파일:\n" + "\n".join(f"- `{source}`" for source in result.sources))
    if result.limitations:
        sections.append(
            "한계:\n" + "\n".join(f"- {limitation}" for limitation in result.limitations)
        )
    return "\n\n".join(sections)

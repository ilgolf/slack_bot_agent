"""Factories for the optional code-agent SDK runners (plan.md Phase 12).

The SDKs are optional extras, so each runner module is imported lazily and a missing
SDK becomes a configuration error that says how to install it.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from src.agent import AnalysisAgentError

if TYPE_CHECKING:
    from src.claude_sdk_runner import ClaudeSdkRunner
    from src.codex_sdk_runner import CodexSdkRunner


def create_claude_runner() -> ClaudeSdkRunner:
    try:
        from src.claude_sdk_runner import ClaudeSdkRunner
    except ImportError:
        raise AnalysisAgentError(
            "claude-agent-sdk가 설치되어 있지 않습니다. "
            "`pip install 'piplup-agent-v2[claude]'`로 설치하세요."
        ) from None
    return ClaudeSdkRunner()


def create_codex_runner() -> CodexSdkRunner:
    try:
        from src.codex_sdk_runner import CodexSdkRunner, codex_thread_starter
    except ImportError:
        raise AnalysisAgentError(
            "openai-codex-sdk가 설치되어 있지 않습니다. "
            "`pip install 'piplup-agent-v2[codex]'`로 설치하세요."
        ) from None
    codex_path = shutil.which("codex")
    if codex_path is None:
        raise AnalysisAgentError(
            "codex 실행 파일을 찾을 수 없습니다. Codex CLI를 설치하고 PATH에 추가하세요."
        )
    return CodexSdkRunner(start_thread=codex_thread_starter(codex_path))

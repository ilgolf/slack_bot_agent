"""Runner factories: turn a missing optional SDK into a clear configuration error
(plan.md Phase 12).
"""

from __future__ import annotations

import sys

import pytest

from src.code.agent import AnalysisAgentError
from src.code.runners import create_claude_runner, create_codex_runner


def test_create_claude_runner_explains_how_to_install_a_missing_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)
    monkeypatch.delitem(sys.modules, "src.code.claude_sdk_runner", raising=False)

    with pytest.raises(AnalysisAgentError, match="claude-agent-sdk"):
        create_claude_runner()


def test_create_codex_runner_explains_how_to_install_a_missing_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "openai_codex_sdk", None)
    monkeypatch.delitem(sys.modules, "src.code.codex_sdk_runner", raising=False)

    with pytest.raises(AnalysisAgentError, match="openai-codex-sdk"):
        create_codex_runner()


def test_create_codex_runner_explains_how_to_install_a_missing_codex_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)

    with pytest.raises(AnalysisAgentError, match="codex"):
        create_codex_runner()

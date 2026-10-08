"""get_agent: selects `FakeAnalysisAgent` by default and a `LangChainAnalysisAgent`
for a configured LLM provider (see plan.md Section 5), mirroring v1's `get_agent`
seam.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.code.agent import FakeAnalysisAgent, get_agent
from src.code.analysis import CodeAgentAnalysisAgent
from src.code.analysis_tools import find_files, list_files, list_projects, read_file
from src.code.claude_sdk_runner import ClaudeSdkRunner
from src.code.codex_sdk_runner import CodexSdkRunner
from src.code.langchain_agent import LangChainAnalysisAgent
from src.core.agent_trace import ThreadTraceStore
from src.core.config import Settings


def test_get_agent_returns_fake_by_default() -> None:
    settings = Settings(anthropic_api_key=None, openai_api_key=None)

    agent = get_agent("fake", settings=settings)

    assert isinstance(agent, FakeAnalysisAgent)


def test_get_agent_returns_langchain_agent_for_anthropic() -> None:
    settings = Settings(anthropic_api_key="sk-ant-fake")

    agent = get_agent("anthropic", settings=settings)

    assert isinstance(agent, LangChainAnalysisAgent)
    assert agent.tool_funcs == [list_projects, read_file, list_files, find_files]


def test_get_agent_returns_langchain_agent_for_openai() -> None:
    settings = Settings(openai_api_key="sk-fake")

    agent = get_agent("openai", settings=settings)

    assert isinstance(agent, LangChainAnalysisAgent)
    assert agent.tool_funcs == [list_projects, read_file, list_files, find_files]


def test_single_provider_is_shared_by_analysis_and_planning_roles() -> None:
    settings = Settings(openai_api_key="sk-fake", llm_provider="openai")

    agent = get_agent(settings.llm_provider, settings=settings)

    assert isinstance(agent, LangChainAnalysisAgent)

    configured_agent = get_agent("openai", settings=settings)

    assert isinstance(configured_agent, LangChainAnalysisAgent)
    assert configured_agent.planning_model is configured_agent.chat_model


def test_get_agent_wires_a_thread_trace_store_for_real_deployment() -> None:
    """Slack/HTTP entry points build one agent per process and share it across
    every thread, so that one agent must already own a working
    `ThreadTraceStore` — not rely on a caller to remember to pass one."""
    settings = Settings(anthropic_api_key="sk-ant-fake")

    agent = get_agent("anthropic", settings=settings)

    assert isinstance(agent, LangChainAnalysisAgent)
    assert isinstance(agent.thread_trace_store, ThreadTraceStore)


def test_llm_model_setting_selects_the_openai_model() -> None:
    settings = Settings(openai_api_key="sk-fake", llm_model="gpt-4o")

    agent = get_agent("openai", settings=settings)

    assert isinstance(agent, LangChainAnalysisAgent)
    assert agent.chat_model.model_name == "gpt-4o"  # type: ignore[attr-defined]


def test_llm_model_setting_selects_the_anthropic_model() -> None:
    settings = Settings(anthropic_api_key="sk-ant-fake", llm_model="claude-sonnet-4-5")

    agent = get_agent("anthropic", settings=settings)

    assert isinstance(agent, LangChainAnalysisAgent)
    assert agent.chat_model.model == "claude-sonnet-4-5"  # type: ignore[attr-defined]


def test_default_models_are_kept_without_llm_model_setting() -> None:
    openai_agent = get_agent("openai", settings=Settings(openai_api_key="sk-fake", llm_model=None))
    anthropic_agent = get_agent(
        "anthropic", settings=Settings(anthropic_api_key="sk-ant-fake", llm_model=None)
    )

    assert openai_agent.chat_model.model_name == "gpt-4o-mini"  # type: ignore[attr-defined]
    assert anthropic_agent.chat_model.model == "claude-3-5-sonnet-latest"  # type: ignore[attr-defined]


def test_get_agent_returns_a_claude_sdk_code_agent_for_claude_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)  # no `.env` here
    settings = Settings(agent_runner_timeout_seconds=42.0, agent_runner_max_turns=7)

    agent = get_agent("claude_code", settings=settings)

    assert isinstance(agent, CodeAgentAnalysisAgent)
    assert isinstance(agent.runner, ClaudeSdkRunner)
    assert (agent.timeout_seconds, agent.max_turns) == (42.0, 7)


def test_get_agent_returns_a_codex_sdk_code_agent_for_codex(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/codex")
    monkeypatch.chdir(tmp_path)  # no `.env` here
    settings = Settings(agent_runner_timeout_seconds=42.0, agent_runner_max_turns=7)

    agent = get_agent("codex", settings=settings)

    assert isinstance(agent, CodeAgentAnalysisAgent)
    assert isinstance(agent.runner, CodexSdkRunner)
    assert (agent.timeout_seconds, agent.max_turns) == (42.0, 7)


def test_get_agent_rejects_an_unknown_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)  # no `.env` here

    with pytest.raises(ValueError, match="unknown LLM provider"):
        get_agent("nope", settings=Settings())


def test_code_agent_receives_the_edit_limits_from_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        agent_edit_timeout_seconds=111.0,
        agent_edit_max_turns=22,
        agent_edit_max_budget_usd=1.5,
    )

    agent = get_agent("claude_code", settings=settings)

    assert isinstance(agent, CodeAgentAnalysisAgent)
    assert (agent.edit_timeout_seconds, agent.edit_max_turns, agent.edit_max_budget_usd) == (
        111.0,
        22,
        1.5,
    )

"""get_agent: selects `FakeAnalysisAgent` by default and a `LangChainAnalysisAgent`
for a configured LLM provider (see plan.md Section 5), mirroring v1's `get_agent`
seam.
"""

from __future__ import annotations

from src.agent import FakeAnalysisAgent, get_agent
from src.agent_trace import ThreadTraceStore
from src.config import Settings
from src.langchain_agent import LangChainAnalysisAgent
from src.tools import find_files, list_files, list_projects, read_file


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

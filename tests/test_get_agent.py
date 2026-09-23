"""get_agent: selects `FakeAnalysisAgent` by default and a `LangChainAnalysisAgent`
for a configured LLM provider (see plan.md Section 5), mirroring v1's `get_agent`
seam.
"""

from __future__ import annotations

from src.agent import FakeAnalysisAgent, get_agent
from src.config import Settings
from src.langchain_agent import LangChainAnalysisAgent
from src.tools import list_files, list_projects, read_file


def test_get_agent_returns_fake_by_default() -> None:
    settings = Settings(anthropic_api_key=None, openai_api_key=None)

    agent = get_agent("fake", settings=settings)

    assert isinstance(agent, FakeAnalysisAgent)


def test_get_agent_returns_langchain_agent_for_anthropic() -> None:
    settings = Settings(anthropic_api_key="sk-ant-fake")

    agent = get_agent("anthropic", settings=settings)

    assert isinstance(agent, LangChainAnalysisAgent)
    assert agent.tool_funcs == [list_projects, read_file, list_files]


def test_get_agent_returns_langchain_agent_for_openai() -> None:
    settings = Settings(openai_api_key="sk-fake")

    agent = get_agent("openai", settings=settings)

    assert isinstance(agent, LangChainAnalysisAgent)
    assert agent.tool_funcs == [list_projects, read_file, list_files]

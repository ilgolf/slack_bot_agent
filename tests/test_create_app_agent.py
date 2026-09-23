"""create_app: builds its agent via `get_agent(settings.llm_provider, ...)` when no
agent is injected (see plan.md Section 7). No real Anthropic/OpenAI API call is made
— constructing a real chat model client doesn't itself touch the network.
"""

from __future__ import annotations

from src.agent import FakeAnalysisAgent
from src.config import Settings
from src.langchain_agent import LangChainAnalysisAgent
from src.main import create_app


def test_create_app_defaults_to_fake_agent_when_llm_provider_is_fake() -> None:
    settings = Settings(llm_provider="fake")

    app = create_app(settings=settings)

    assert isinstance(app.state.agent, FakeAnalysisAgent)


def test_create_app_uses_get_agent_for_configured_provider() -> None:
    settings = Settings(llm_provider="anthropic", anthropic_api_key="sk-ant-fake")

    app = create_app(settings=settings)

    assert isinstance(app.state.agent, LangChainAnalysisAgent)

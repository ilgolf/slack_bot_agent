"""create_app: builds its agent via `get_agent(settings.llm_provider, ...)` when no
agent is injected (see plan.md Section 7). No real Anthropic/OpenAI API call is made
— constructing a real chat model client doesn't itself touch the network.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, BaseMessage

from src.agent import FakeAnalysisAgent
from src.config import Settings
from src.langchain_agent import LangChainAnalysisAgent
from src.main import create_app
from src.project_resolver import ProjectResolver


def test_create_app_defaults_to_fake_agent_when_llm_provider_is_fake() -> None:
    settings = Settings(llm_provider="fake")

    app = create_app(settings=settings)

    assert isinstance(app.state.agent, FakeAnalysisAgent)


def test_create_app_uses_get_agent_for_configured_provider() -> None:
    settings = Settings(llm_provider="anthropic", anthropic_api_key="sk-ant-fake")

    app = create_app(settings=settings)

    assert isinstance(app.state.agent, LangChainAnalysisAgent)


def test_create_app_routes_unmatched_request_to_code_work_via_llm_classifier(
    tmp_path: Path,
) -> None:
    """Rules leave "계획서 내용대로 슬슬 달려보자구" as analysis; the agent's
    model classifying it as code_work must send it down the code-work path."""

    class _CodeWorkModel:
        def invoke(self, input: list[BaseMessage]) -> AIMessage:
            return AIMessage(content="code_work")

    agent = LangChainAnalysisAgent(
        chat_model=_CodeWorkModel(), project_resolver=ProjectResolver(root=tmp_path)
    )
    app = create_app(
        settings=Settings(
            llm_provider="fake",
            thread_context_root=str(tmp_path / "context"),
            projects_root=str(tmp_path),
        ),
        agent=agent,
    )

    response = TestClient(app).post(
        "/debug/command",
        json={"channel_id": "C1", "thread_ts": "1.1", "text": "계획서 내용대로 슬슬 달려보자구"},
    )

    assert response.json() == {"response": "코드 작업할 대상 프로젝트명을 요청에 포함해 주세요."}

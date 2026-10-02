"""FastAPI application entrypoint.

Run locally with::

    uv run uvicorn src.main:app --reload

``POST /debug/command`` takes a plain JSON body for manual testing without Slack.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from fastapi import FastAPI, Request, Response
from pydantic import BaseModel
from slack_bolt.adapter.fastapi import SlackRequestHandler

from src.agent import AnalysisAgent, get_agent
from src.artifact_generation import ArtifactGenerationWorkflow
from src.config import Settings, get_settings
from src.linear_workflow import LinearIntegrationWorkflow
from src.llm_intent_classifier import build_intent_classifier
from src.request_coordinator import RequestCoordinator
from src.request_router import RequestRouter
from src.run_state import ThreadRunStore
from src.slack_app import SlackConfigError, build_slack_app, handle_app_mention
from src.thread_context import ThreadContextStore
from src.workflow_factory import build_execution_workflow


class DebugCommand(BaseModel):
    channel_id: str
    thread_ts: str
    text: str


def create_app(
    *,
    settings: Settings | None = None,
    thread_context: ThreadContextStore | None = None,
    agent: AnalysisAgent | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    thread_context = thread_context or ThreadContextStore(root=settings.thread_context_root)
    agent = agent or get_agent(settings.llm_provider, settings=settings)
    execution_workflow = build_execution_workflow(settings)
    artifact_workflow = ArtifactGenerationWorkflow()
    linear_workflow = LinearIntegrationWorkflow(settings=settings)
    coordinator = RequestCoordinator(
        router=RequestRouter(intent_classifier=build_intent_classifier(agent)),
        execution_workflow=execution_workflow,
        artifact_workflow=artifact_workflow,
        linear_workflow=linear_workflow,
    )

    try:
        slack_app = build_slack_app(settings)
    except SlackConfigError:
        slack_app = None

    if slack_app is not None:
        run_store = ThreadRunStore()

        @slack_app.event("app_mention")
        def _on_app_mention(event: Mapping[str, Any], say: Callable[..., Any]) -> None:
            handle_app_mention(
                event,
                say,
                thread_context=thread_context,
                agent=agent,
                run_store=run_store,
                coordinator=coordinator,
            )

    app = FastAPI(title="piplup-agent-v2", version="0.1.0")
    app.state.agent = agent

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/debug/command")
    def debug_command(command: DebugCommand) -> dict[str, str]:
        _, response = coordinator.process(
            channel_id=command.channel_id,
            thread_ts=command.thread_ts,
            text=command.text,
            thread_context=thread_context,
            agent=agent,
        )
        return {"response": response}

    if slack_app is not None:
        handler = SlackRequestHandler(slack_app)

        @app.post("/slack/events")
        async def slack_events(request: Request) -> Response:
            return await handler.handle(request)

    return app


app = create_app()

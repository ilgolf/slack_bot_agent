"""Slack Bolt integration.

This project runs its own separate Slack app (own bot token/signing secret), so it
can run side by side with v1 during migration — see plan.md's design summary.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from src.agent import AnalysisAgent
from src.artifact_generation import ArtifactGenerationWorkflow
from src.config import Settings
from src.dispatch import dispatch_command
from src.execution_workflow import ExecutionWorkflow, SkillRegistry
from src.linear_workflow import LinearIntegrationWorkflow
from src.observability import request_log_context
from src.project_resolver import ProjectResolver
from src.run_state import ThreadRunStore
from src.thread_context import ThreadContextStore


class SlackConfigError(RuntimeError):
    """Raised when Slack credentials needed to build the Bolt app are missing."""


def handle_app_mention(
    event: Mapping[str, Any],
    say: Callable[..., Any],
    *,
    thread_context: ThreadContextStore,
    agent: AnalysisAgent,
    run_store: ThreadRunStore | None = None,
    artifact_workflow: ArtifactGenerationWorkflow | None = None,
    execution_workflow: ExecutionWorkflow | None = None,
    linear_workflow: LinearIntegrationWorkflow | None = None,
) -> None:
    channel_id = event["channel"]
    thread_ts = event.get("thread_ts", event["ts"])
    text = event["text"]
    event_id = str(event.get("event_id", event["ts"]))

    if run_store is not None and not run_store.begin(
        event_id=event_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
    ):
        return

    say(text="분석 중입니다…", thread_ts=thread_ts)
    if run_store is not None:
        run_store.set_running(channel_id=channel_id, thread_ts=thread_ts)

    with request_log_context(uuid4().hex, channel_id, thread_ts):
        try:
            response = None
            if execution_workflow is not None:
                response = execution_workflow.process(
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    text=text,
                    thread_context=thread_context,
                    agent=agent,
                    defer_missing_confirmation=True,
                )
            if linear_workflow is not None:
                response = response or linear_workflow.process(
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    text=text,
                    thread_context=thread_context,
                    agent=agent,
                )
            if artifact_workflow is not None:
                response = response or artifact_workflow.process(
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    text=text,
                    thread_context=thread_context,
                    agent=agent,
                )
            if response is None:
                response = dispatch_command(
                    channel_id,
                    thread_ts,
                    text,
                    thread_context=thread_context,
                    agent=agent,
                )
            else:
                thread_context.append(channel_id, thread_ts, text)
                thread_context.append(channel_id, thread_ts, response)
        except Exception:
            if run_store is not None:
                run_store.fail(channel_id=channel_id, thread_ts=thread_ts)
            raise

    say(text=response, thread_ts=thread_ts)
    if run_store is not None:
        run_store.complete(channel_id=channel_id, thread_ts=thread_ts)


def build_slack_app(settings: Settings) -> App:
    if not settings.slack_bot_token:
        raise SlackConfigError("SLACK_BOT_TOKEN is required to run the Slack app")

    return App(
        token=settings.slack_bot_token,
        signing_secret=settings.slack_signing_secret,
        token_verification_enabled=False,
    )


def start_socket_mode(
    settings: Settings,
    *,
    thread_context: ThreadContextStore,
    agent: AnalysisAgent,
) -> None:
    """Run the Slack app through Socket Mode until the process is stopped."""
    if not settings.slack_app_token:
        raise SlackConfigError("SLACK_APP_TOKEN is required to run Socket Mode")

    slack_app = build_slack_app(settings)
    run_store = ThreadRunStore()
    artifact_workflow = ArtifactGenerationWorkflow()
    linear_workflow = LinearIntegrationWorkflow(settings=settings)
    execution_workflow = ExecutionWorkflow(
        project_resolver=ProjectResolver(root=Path(settings.projects_root).expanduser()),
        skill_registry=SkillRegistry({"codex": "~/.codex/skills"}),
    )

    @slack_app.event("app_mention")
    def _on_app_mention(event: Mapping[str, Any], say: Callable[..., Any]) -> None:
        handle_app_mention(
            event,
            say,
            thread_context=thread_context,
            agent=agent,
            run_store=run_store,
            artifact_workflow=artifact_workflow,
            execution_workflow=execution_workflow,
            linear_workflow=linear_workflow,
        )

    @slack_app.event("message")
    def _ignore_message_event() -> None:
        """Acknowledge subscribed message events that this bot does not analyze."""

    SocketModeHandler(slack_app, settings.slack_app_token).start()  # type: ignore[no-untyped-call]

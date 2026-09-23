"""create_app: wires POST /slack/events onto the Bolt request handler only when
Slack credentials are configured (see plan.md Section 4).
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from src.config import Settings
from src.main import create_app


def test_slack_events_route_present_when_credentials_configured() -> None:
    settings = Settings(slack_bot_token="xoxb-fake", slack_signing_secret="shhh")

    app = create_app(settings=settings)

    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    assert "/slack/events" in paths


def test_slack_events_route_absent_without_credentials() -> None:
    settings = Settings(slack_bot_token=None, slack_signing_secret=None)

    app = create_app(settings=settings)

    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    assert "/slack/events" not in paths

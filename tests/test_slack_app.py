"""Slack Bolt integration: this project runs its own separate Slack app (own bot
token/signing secret), so it can run side by side with v1 during migration (see
plan.md's design summary).
"""

from __future__ import annotations

import pytest
from slack_bolt import App

from src.config import Settings
from src.slack_app import SlackConfigError, build_slack_app, start_socket_mode


def test_build_slack_app_returns_bolt_app_when_configured() -> None:
    settings = Settings(slack_bot_token="xoxb-fake", slack_signing_secret="shhh")

    app = build_slack_app(settings)

    assert isinstance(app, App)


def test_build_slack_app_raises_when_credentials_missing() -> None:
    settings = Settings(slack_bot_token=None, slack_signing_secret=None)

    with pytest.raises(SlackConfigError):
        build_slack_app(settings)


def test_socket_mode_requires_an_app_token() -> None:
    settings = Settings(slack_bot_token="xoxb-fake", slack_app_token=None)

    with pytest.raises(SlackConfigError, match="SLACK_APP_TOKEN"):
        start_socket_mode(
            settings,
            thread_context=object(),  # type: ignore[arg-type]
            agent=object(),  # type: ignore[arg-type]
        )

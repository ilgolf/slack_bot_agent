"""Slack Bolt integration: this project runs its own separate Slack app (own bot
token/signing secret), so it can run side by side with v1 during migration (see
plan.md's design summary).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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


def test_socket_mode_handler_passes_slack_client_for_progress_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src import slack_app
    from src.thread_context import ThreadContextStore
    from tests.test_autopilot import SequencedPlanningAgent, _write_plan

    handlers: dict[str, Any] = {}

    class FakeApp:
        def event(self, name: str) -> Any:
            def register(func: Any) -> Any:
                handlers[name] = func
                return func

            return register

    class FakeSocketModeHandler:
        def __init__(self, *_args: Any) -> None:
            pass

        def start(self) -> None:
            pass

    updates: list[dict[str, Any]] = []

    class FakeClient:
        def chat_update(self, **kwargs: Any) -> None:
            updates.append(kwargs)

    monkeypatch.setattr(slack_app, "build_slack_app", lambda _settings: FakeApp())
    monkeypatch.setattr(slack_app, "SocketModeHandler", FakeSocketModeHandler)
    project = tmp_path / "my-project"
    project.mkdir()
    (project / "plan.md").write_text("- [ ] first\n")
    settings = Settings(
        slack_bot_token="xoxb-fake", slack_app_token="xapp-fake", projects_root=str(tmp_path)
    )

    start_socket_mode(
        settings,
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=SequencedPlanningAgent([_write_plan("src/a.txt", "A\n")]),  # type: ignore[arg-type]
    )
    handlers["app_mention"](
        event={"channel": "C1", "ts": "1.1", "text": "my-project plan.md 기준으로 끝까지 진행해"},
        say=lambda **_kwargs: {"ts": "2.2"},
        client=FakeClient(),
    )

    assert updates == [{"channel": "C1", "ts": "2.2", "text": "🔄 자동 진행 1/1 완료"}]

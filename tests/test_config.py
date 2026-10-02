"""Settings: configuration loaded from environment variables."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.config import Settings, resolve_code_work_mode


def test_environment_defaults_to_local(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.chdir(tmp_path)

    assert Settings().environment == "local"


def test_environment_reads_from_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.chdir(tmp_path)

    assert Settings().environment == "production"


def test_llm_provider_defaults_to_fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.chdir(tmp_path)

    assert Settings().llm_provider == "fake"


def test_llm_provider_reads_from_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.chdir(tmp_path)

    assert Settings().llm_provider == "anthropic"


def test_slack_app_token_reads_from_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    monkeypatch.chdir(tmp_path)

    assert Settings().slack_app_token == "xapp-test"


def test_linear_settings_read_from_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_test")
    monkeypatch.setenv("LINEAR_TIMEOUT_SECONDS", "12.5")
    monkeypatch.chdir(tmp_path)

    settings = Settings()

    assert settings.linear_api_key == "lin_api_test"
    assert settings.linear_timeout_seconds == 12.5


def test_agent_runner_limits_default_to_the_code_agent_adapter_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("AGENT_RUNNER_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("AGENT_RUNNER_MAX_TURNS", raising=False)
    monkeypatch.chdir(tmp_path)

    settings = Settings()

    assert settings.agent_runner_timeout_seconds == 300.0
    assert settings.agent_runner_max_turns == 20


def test_worktrees_root_defaults_to_a_directory_outside_projects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("WORKTREES_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)

    assert Settings().worktrees_root == "~/.slack_bot_agent/worktrees"


def test_worktrees_root_reads_from_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WORKTREES_ROOT", "/tmp/custom-worktrees")
    monkeypatch.chdir(tmp_path)

    assert Settings().worktrees_root == "/tmp/custom-worktrees"


def test_edit_mode_settings_have_safe_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in (
        "CODE_WORK_MODE",
        "AGENT_EDIT_TIMEOUT_SECONDS",
        "AGENT_EDIT_MAX_TURNS",
        "AGENT_EDIT_MAX_BUDGET_USD",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)

    settings = Settings()

    assert settings.code_work_mode == "plan"
    assert settings.agent_edit_timeout_seconds == 600.0
    assert settings.agent_edit_max_turns == 40
    assert settings.agent_edit_max_budget_usd == 3.0


def test_edit_mode_settings_read_from_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CODE_WORK_MODE", "edit")
    monkeypatch.setenv("AGENT_EDIT_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("AGENT_EDIT_MAX_TURNS", "12")
    monkeypatch.setenv("AGENT_EDIT_MAX_BUDGET_USD", "0.5")
    monkeypatch.chdir(tmp_path)

    settings = Settings()

    assert settings.code_work_mode == "edit"
    assert (
        settings.agent_edit_timeout_seconds,
        settings.agent_edit_max_turns,
        settings.agent_edit_max_budget_usd,
    ) == (120.0, 12, 0.5)


def test_an_unknown_code_work_mode_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CODE_WORK_MODE", "yolo")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValidationError):
        Settings()


def test_edit_mode_is_unavailable_when_worktrees_live_under_a_dot_claude_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    blocked = Settings(code_work_mode="edit", worktrees_root="~/.claude/jobs/x/worktrees")
    fine = Settings(code_work_mode="edit", worktrees_root=str(tmp_path))
    planning = Settings(code_work_mode="plan", worktrees_root="~/.claude/w")

    mode, reason = resolve_code_work_mode(blocked)
    assert mode == "plan" and reason is not None and ".claude" in reason
    assert resolve_code_work_mode(fine) == ("edit", None)
    assert resolve_code_work_mode(planning) == ("plan", None)

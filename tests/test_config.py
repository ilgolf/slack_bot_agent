"""Settings: configuration loaded from environment variables."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Settings


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

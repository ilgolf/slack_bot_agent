"""Socket Mode process logging."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from src.socket_mode import configure_logging


def test_configure_logging_writes_to_rotating_log_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    configure_logging("INFO")
    logging.getLogger("socket-mode-test").info("socket mode started")

    output = (tmp_path / "logs" / "slack-bot.log").read_text()
    assert "socket mode started" in output
    assert "request_id=-" in output

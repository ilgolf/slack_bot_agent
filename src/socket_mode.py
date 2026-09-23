"""Socket Mode entrypoint for receiving Slack events without a public HTTP URL."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.agent import get_agent
from src.config import get_settings
from src.observability import RequestContextFilter
from src.slack_app import start_socket_mode
from src.thread_context import ThreadContextStore


def configure_logging(log_level: str) -> None:
    """Write Socket Mode events to the console and a rotating local log file."""
    log_path = Path("logs/slack-bot.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s "
        "[request_id=%(request_id)s channel=%(channel_id)s thread=%(thread_ts)s]: %(message)s"
    )
    context_filter = RequestContextFilter()

    console_handler = logging.StreamHandler()
    console_handler.addFilter(context_filter)
    console_handler.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.addFilter(context_filter)
    file_handler.setFormatter(formatter)

    logging.basicConfig(
        level=log_level.upper(),
        handlers=[console_handler, file_handler],
        force=True,
    )


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    start_socket_mode(
        settings,
        thread_context=ThreadContextStore(root=settings.thread_context_root),
        agent=get_agent(settings.llm_provider, settings=settings),
    )


if __name__ == "__main__":
    main()

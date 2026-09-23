"""Application configuration loaded from environment variables.

Secrets are never hardcoded; every value comes from the environment or an
optional local ``.env`` file (see ``.env.example``).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "local"
    log_level: str = "INFO"

    # Root directory that local projects are resolved under (see src.project_resolver)
    projects_root: str = "~/orca/projects"

    # Root directory for the per-thread markdown context store (see src.thread_context)
    thread_context_root: str = "./thread-context"

    # Slack — a SEPARATE Slack app/bot from piplup-agent (v1)
    slack_bot_token: str | None = None
    slack_signing_secret: str | None = None
    slack_app_token: str | None = None

    # LLM provider for the tool-calling analysis agent (see src.agent.get_agent)
    llm_provider: str = "fake"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None

    # Linear — a Personal API key for this local PC installation only.
    linear_api_key: str | None = None
    linear_api_url: str = "https://api.linear.app/graphql"
    linear_timeout_seconds: float = 10.0


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return a cached ``Settings`` instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings

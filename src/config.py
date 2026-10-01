"""Application configuration loaded from environment variables.

Secrets are never hardcoded; every value comes from the environment or an
optional local ``.env`` file (see ``.env.example``).
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class LLMRoleProviders:
    """Explicit provider selections for the two LLM workflow roles."""

    analysis_plan: str | None
    code_execution: str | None


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

    # Legacy provider shared by both roles when role-specific selection is absent.
    llm_provider: str = "fake"
    # Optional role-specific provider selections. Resolution and fallback are handled
    # separately so configuration loading preserves whether a role was explicit.
    analysis_plan_llm_provider: str | None = None
    code_execution_llm_provider: str | None = None
    # Overrides the provider's default chat model (see src.agent.get_agent)
    llm_model: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    agent_max_tool_iterations: int = 16

    # Linear — a Personal API key for this local PC installation only.
    linear_api_key: str | None = None
    linear_api_url: str = "https://api.linear.app/graphql"
    linear_timeout_seconds: float = 10.0

    @property
    def llm_role_providers(self) -> LLMRoleProviders:
        """Return explicit provider settings grouped by workflow role."""
        return LLMRoleProviders(
            analysis_plan=self.analysis_plan_llm_provider,
            code_execution=self.code_execution_llm_provider,
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return a cached ``Settings`` instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings

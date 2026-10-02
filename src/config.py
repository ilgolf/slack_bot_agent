"""Application configuration loaded from environment variables.

Secrets are never hardcoded; every value comes from the environment or an
optional local ``.env`` file (see ``.env.example``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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

    # Root directory for per-thread git worktrees of code work (see src.thread_workspace)
    worktrees_root: str = "~/.slack_bot_agent/worktrees"

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

    # Limits for code-agent runners (`claude_code`, `codex`; see src.code_agent_analysis)
    agent_runner_timeout_seconds: float = 300.0
    agent_runner_max_turns: int = 20

    # Code work: "plan" (the agent proposes a plan, the workflow writes it after `실행`) or
    # "edit" (a claude_code agent edits the thread worktree directly; see resolve_code_work_mode).
    code_work_mode: Literal["plan", "edit"] = "plan"
    # Limits for one agent editing run (see src.claude_sdk_runner.edit_options)
    agent_edit_timeout_seconds: float = 600.0
    agent_edit_max_turns: int = 40
    agent_edit_max_budget_usd: float = 3.0

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


def resolve_code_work_mode(settings: Settings) -> tuple[Literal["plan", "edit"], str | None]:
    """The mode to run with, plus why it differs from the configured one.

    Claude Code refuses every Write/Edit under a `.claude` directory, so edit mode cannot
    work when the worktrees live there; plan mode keeps working."""
    if settings.code_work_mode != "edit":
        return settings.code_work_mode, None
    parts = Path(settings.worktrees_root).expanduser().parts
    if ".claude" in parts:
        return "plan", (
            "CODE_WORK_MODE=edit는 WORKTREES_ROOT가 .claude 디렉터리 아래라 쓸 수 없어 "
            "plan 모드로 실행합니다. WORKTREES_ROOT를 .claude 밖으로 옮겨 주세요."
        )
    return "edit", None


def get_settings() -> Settings:
    """Return a cached ``Settings`` instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings

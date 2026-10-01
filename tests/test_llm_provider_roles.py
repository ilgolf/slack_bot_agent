from __future__ import annotations

from src.config import LLMRoleProviders, Settings


def test_role_specific_llm_provider_settings_are_exposed_by_role() -> None:
    settings = Settings(
        analysis_plan_llm_provider="anthropic",
        code_execution_llm_provider="openai",
    )

    assert settings.llm_role_providers == LLMRoleProviders(
        analysis_plan="anthropic",
        code_execution="openai",
    )

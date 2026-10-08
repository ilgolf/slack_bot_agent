"""Central categories and confirmation rules for tool execution."""

from __future__ import annotations

from enum import StrEnum


class ToolCategory(StrEnum):
    PROJECT_READ = "project_read"
    PROJECT_SEARCH = "project_search"
    GIT_READ = "git_read"
    VERIFY = "verify"
    PROJECT_WRITE = "project_write"
    EXTERNAL_READ = "external_read"
    EXTERNAL_MUTATION = "external_mutation"
    BLOCKED = "blocked"


class ConfirmationMode(StrEnum):
    NONE = "none"
    THREAD_CONFIRMATION = "thread_confirmation"
    DENY = "deny"


def requires_confirmation(category: ToolCategory) -> ConfirmationMode:
    if category in {
        ToolCategory.PROJECT_WRITE,
        ToolCategory.VERIFY,
        ToolCategory.EXTERNAL_MUTATION,
    }:
        return ConfirmationMode.THREAD_CONFIRMATION
    if category is ToolCategory.BLOCKED:
        return ConfirmationMode.DENY
    return ConfirmationMode.NONE

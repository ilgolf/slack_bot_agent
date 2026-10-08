"""Typed allowlist of tools available to the code-agent loop."""

from __future__ import annotations

import inspect
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.code.tool_policy import ConfirmationMode, ToolCategory, requires_confirmation


@dataclass(frozen=True)
class ToolArgument:
    name: str
    type: Any
    required: bool = True


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    category: ToolCategory
    handler: Callable[..., Any]
    confirmation: ConfirmationMode
    timeout_seconds: int = 30
    max_result_bytes: int = 32_000
    arguments: tuple[ToolArgument, ...] = field(default_factory=tuple)
    evidence_arg: str | None = None


@dataclass(frozen=True)
class ToolOutcome:
    status: str
    safe_message: str
    evidence: tuple[str, ...] = ()
    retryable: bool = False


class ToolRegistry:
    """An explicit registry; unknown or blocked tools cannot be executed."""

    def __init__(self, definitions: list[ToolDefinition]) -> None:
        self._definitions = {definition.name: definition for definition in definitions}

    def get(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    def definitions(self) -> list[ToolDefinition]:
        return list(self._definitions.values())

    def invoke(
        self, name: str, args: dict[str, Any] | None = None, *, confirmed: bool = False
    ) -> ToolOutcome:
        args = args or {}
        definition = self.get(name)
        if definition is None:
            return ToolOutcome("blocked", "등록되지 않은 도구입니다.")
        if definition.confirmation is ConfirmationMode.DENY:
            return ToolOutcome("blocked", "정책상 허용되지 않은 도구입니다.")
        if definition.confirmation is ConfirmationMode.THREAD_CONFIRMATION and not confirmed:
            return ToolOutcome("awaiting_confirmation", "Slack 스레드 실행 확인이 필요합니다.")
        try:
            result = definition.handler(**args)
        except subprocess.TimeoutExpired:
            return ToolOutcome("timeout", "실행 시간이 초과되었습니다.", retryable=False)
        except (OSError, ValueError) as exc:
            return ToolOutcome("failed", type(exc).__name__, retryable=False)
        except TypeError as exc:
            return ToolOutcome("failed", type(exc).__name__, retryable=True)
        safe = str(result)
        if len(safe.encode("utf-8")) > definition.max_result_bytes:
            safe = safe.encode("utf-8")[: definition.max_result_bytes].decode("utf-8", "ignore")
            return ToolOutcome("truncated", safe, retryable=False)
        evidence_value = args.get(definition.evidence_arg) if definition.evidence_arg else None
        evidence = (evidence_value,) if isinstance(evidence_value, str) else ()
        return ToolOutcome("ok", safe, evidence=evidence)


def _derive_arguments(handler: Callable[..., Any]) -> tuple[ToolArgument, ...]:
    """Introspect `handler`'s signature so call sites don't hand-write schema
    that would drift from the actual function they wrap."""
    # `eval_str=True` resolves string annotations back to real types for
    # callers under `from __future__ import annotations` (PEP 563).
    parameters = inspect.signature(handler, eval_str=True).parameters.values()
    return tuple(
        ToolArgument(
            name=parameter.name,
            type=Any if parameter.annotation is inspect.Parameter.empty else parameter.annotation,
            required=parameter.default is inspect.Parameter.empty,
        )
        for parameter in parameters
        if parameter.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    )


def definition(
    name: str,
    category: ToolCategory,
    handler: Callable[..., Any],
    *,
    evidence_arg: str | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        name,
        category,
        handler,
        requires_confirmation(category),
        arguments=_derive_arguments(handler),
        evidence_arg=evidence_arg,
    )

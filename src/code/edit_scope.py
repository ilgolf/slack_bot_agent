"""What one direct-edit run may touch (plan.md Phases 18, 28 and 34).

Pure decisions from the user's own words and the worktree: which files they named, what a
plan-first project lets this run write, and which guidance the edit run was handed.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from src.code.markers import PLAN_AUTHORING_MARKERS
from src.core.plan_first import (
    CONFIRMED,
    detect_area,
    plan_first_enabled,
    plan_path,
    plan_status,
    wants_bypass,
)
from src.core.plan_guard import is_protected_meta_path
from src.core.project_guidance import guidance_files
from src.core.skill_allowlist import claude_skill_names

# Accept a Korean postposition directly after a filename (``plan.md에``)
# while returning only the project-relative path.
PATH_IN_REQUEST = re.compile(r"(?<!\S)([\w./-]+\.[A-Za-z0-9]+)(?=$|[\s,.:!?…]|[은는이가을를에의])")


def edit_named_paths(command_text: str) -> list[str]:
    """Files the user named in this message, which the edit agent may touch even if they are
    management or code-executing files; a plan-authoring request names plan.md."""
    follow = is_plan_follow_request(command_text)
    named = [
        path
        for path in PATH_IN_REQUEST.findall(command_text)
        if not (follow and is_protected_meta_path(path))
    ]
    if not follow and any(marker in command_text.casefold() for marker in PLAN_AUTHORING_MARKERS):
        named.append("plan.md")
    return list(dict.fromkeys(named))


def edit_named_paths_in_thread(
    command_text: str, earlier_user_messages: Sequence[str]
) -> list[str]:
    """`edit_named_paths` plus the management files (`plan.md`, ...) the user themself named
    in earlier messages of this thread, so a follow-up without a file name keeps them
    editable. Only the user's own words count — never bot replies or data the bot read —
    and a plan-follow request keeps `plan.md` read-only whatever was said before."""
    named = edit_named_paths(command_text)
    if is_plan_follow_request(command_text):
        return named
    earlier = [
        path
        for message in earlier_user_messages
        for path in edit_named_paths(message)
        if is_protected_meta_path(path)
    ]
    return list(dict.fromkeys([*named, *earlier]))


def area_plan_for_named_plan(
    worktree: Path, named: frozenset[str], messages: Sequence[str]
) -> frozenset[str]:
    """A bare `plan.md` in an area's conversation (`linear plan.md 수정해`) means that area's
    plan, which lives at `docs/<영역>/plan.md`. Without a detected area nothing is added."""
    if "plan.md" not in named:
        return frozenset()
    area = detect_area(messages)
    if area is None or not plan_path(worktree, area).is_file():
        return frozenset()
    return frozenset({f"docs/{area}/plan.md"})


@dataclass(frozen=True)
class PlanFirstLimits:
    write_roots: tuple[str, ...] | None = None
    named: frozenset[str] = frozenset()
    stage: str | None = None
    area: str | None = None


def plan_first_limits(
    worktree: Path, command_text: str, earlier_user_messages: Sequence[str]
) -> PlanFirstLimits:
    """What a plan-first project allows this run to write (plan.md Phase 28): `docs/` only,
    plus the one area plan the user's words point at, until that plan is confirmed. A project
    that did not opt in is not limited and gets no stage."""
    if not plan_first_enabled(worktree):
        return PlanFirstLimits()
    area = detect_area([*earlier_user_messages, command_text])
    if wants_bypass(command_text):
        # Only this message counts: an earlier "기획 없이" does not carry over to later ones.
        return PlanFirstLimits(stage="bypassed", area=area)
    if area is not None and plan_status(worktree, area) == CONFIRMED:
        return PlanFirstLimits(stage="developing", area=area)
    named = frozenset({f"docs/{area}/plan.md"}) if area is not None else frozenset()
    return PlanFirstLimits(("docs/",), named, "planning", area)


def edit_applied_guidance(worktree: Path) -> tuple[list[str], list[str]]:
    """What the Claude edit run was actually given: the guidance files handed over in the
    prompt (CLAUDE.md is not read) and the allowlisted project skills."""

    def is_plain_file(path: Path) -> bool:
        return path.is_file() and not path.is_symlink()

    agents = guidance_files(worktree)
    skills = [
        f"claude:{name}"
        for name in claude_skill_names(worktree)
        if is_plain_file(worktree / ".claude" / "skills" / name / "SKILL.md")
    ]
    return agents, skills


# Korean postpositions that mean "based on / according to / following" plan.md
# — whichever verb comes after them ("구현해줘", "진행해줘", "작업해줘", ...) is
# unbounded and not worth enumerating, but this small, closed set of relational
# particles is what actually signals "plan.md is a spec to implement" versus
# "plan.md is the thing being written to" (e.g. "plan.md 수정해줘", "plan.md에
# 계획 짜볼래?", both left unprotected, matching direct-edit requests).
_PLAN_REFERENCE_MARKERS = ("대로", "기준으로", "보고", "따라", "맞춰", "확인 후", "확인하고")


def is_plan_follow_request(text: str) -> bool:
    """plan.md referenced with a "following/based on" postposition is a
    read-only spec to implement in code, not a write target — independent of
    which verb names the implementation work."""
    normalized = text.casefold()
    if "plan.md" not in normalized:
        return False
    return any(marker in normalized for marker in _PLAN_REFERENCE_MARKERS)


@dataclass(frozen=True)
class PlanRequest:
    """How one message reads as a request to plan code: which files it points at and which
    of them it authorizes as write targets."""

    explicit_target_paths: list[str]
    target_paths: list[str]
    plan_follow_request: bool
    plan_document_is_evidence: bool
    linear_plan_document: bool
    user_writable_paths: list[str]


def interpret_plan_request(command_text: str, contextual_text: str) -> PlanRequest:
    """`command_text` is this message alone; `contextual_text` adds the thread's history,
    used only where the message itself names nothing."""
    # A file named in this exact message always wins — it's what the user
    # just said, e.g. redirecting a pending plan to a different file. Only
    # fall back to thread context when this message names nothing at all.
    explicit_target_paths = PATH_IN_REQUEST.findall(command_text)
    target_paths = explicit_target_paths or PATH_IN_REQUEST.findall(contextual_text)
    plan_follow_request = is_plan_follow_request(command_text) or (
        not explicit_target_paths and is_plan_follow_request(contextual_text)
    )
    # plan.md is already loaded into planning context via `target_paths`
    # below, so when it's the thing being followed, its own content is
    # sufficient grounding for a brand-new feature that has no prior
    # src/tests code to investigate — the model isn't limited to only
    # modifying what analyze() happened to read.
    plan_document_is_evidence = plan_follow_request and any(
        Path(path).name.casefold() == "plan.md" for path in target_paths
    )
    linear_plan_document = (
        "linear" in command_text.casefold()
        and any(Path(path).name.casefold() == "plan.md" for path in explicit_target_paths)
        and not plan_follow_request
    )
    # "Follow plan.md" names the file as a read-only specification, not
    # as a requested write target.  Keep it in planning context, but do
    # not let that wording authorize the model to overwrite it.
    user_writable_paths = explicit_target_paths
    if plan_follow_request:
        user_writable_paths = [
            path for path in explicit_target_paths if not is_protected_meta_path(path)
        ]
    # Asking for a plan to be written authorizes plan.md as a write target.
    # Every other proposed file still follows the usual rules. Judged from this
    # message alone, never thread context.
    plan_authoring = not plan_follow_request and any(
        marker in command_text.casefold() for marker in PLAN_AUTHORING_MARKERS
    )
    if plan_authoring:
        user_writable_paths = list(dict.fromkeys([*user_writable_paths, "plan.md"]))
    return PlanRequest(
        explicit_target_paths=explicit_target_paths,
        target_paths=target_paths,
        plan_follow_request=plan_follow_request,
        plan_document_is_evidence=plan_document_is_evidence,
        linear_plan_document=linear_plan_document,
        user_writable_paths=user_writable_paths,
    )

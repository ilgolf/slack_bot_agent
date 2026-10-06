"""Plan-first development (plan.md Phase 28): code is written only after the area's
`docs/<area>/plan.md` is confirmed.

The pure reading side lives here: the plan's status line, its open questions, the area a
request is about and whether a project opted in. Gates and the `기획 확정` command build on it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

AREAS = ("linear", "code", "notion")
PLAN_FIRST_MARKER = ".piplup/plan-first"
CONFIRMED: Literal["확정"] = "확정"
DRAFT: Literal["초안"] = "초안"

_STATUS_LINE = re.compile(r"^상태:[ \t]*(\S+)[ \t]*$", re.MULTILINE)
_OPEN_QUESTIONS_HEADING = "## 열린 질문"
_UNCHECKED = re.compile(r"^\s*- \[ \]")
_AREA_MENTION = re.compile(
    r"docs/(?P<doc>linear|code|notion)\b"
    r"|\b(?P<name>linear|notion)\b"
    r"|(?P<code>\bcode[ -]?(?:agent|에이전트|영역)|코드 에이전트)",
    re.IGNORECASE | re.ASCII,
)


_BYPASS = re.compile(
    r"(?:기획|docs|plan-first)(?:\s*단계)?\s*(?:을|를|은|는)?\s*"
    r"(?:우회|없이(?!는)|건너뛰|건너뛴|생략|스킵|skip)",
    re.IGNORECASE,
)
_NEGATION = re.compile(r"하지\s*마|하지\s*말|말고|금지")


def wants_bypass(message: str) -> bool:
    """The user said, in this message, to skip the plan (`기획 없이`, `기획 우회`, ...). A
    negation right after (`우회하지 마`, `건너뛰지 말고`) is not a bypass."""
    return any(
        not _NEGATION.search(message[match.end() : match.end() + 10])
        for match in _BYPASS.finditer(message)
    )


def plan_path(project_root: Path, area: str) -> Path:
    return project_root / "docs" / area / "plan.md"


def plan_status(project_root: Path, area: str) -> Literal["초안", "확정"]:
    """`확정` only when the plan has a `상태: 확정` line; a missing file, a missing line or
    any other value is a draft."""
    try:
        text = plan_path(project_root, area).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return DRAFT
    return parse_status(text)


def parse_status(text: str) -> Literal["초안", "확정"]:
    match = _STATUS_LINE.search(text)
    return CONFIRMED if match is not None and match.group(1) == CONFIRMED else DRAFT


def with_status(text: str, status: str) -> str:
    """`text` with its `상태:` line set to `status`; a plan without one gets the line right
    below its title (or at the top when it has no title)."""
    line = f"상태: {status}"
    if _STATUS_LINE.search(text):
        return _STATUS_LINE.sub(line, text, count=1)
    lines = text.split("\n")
    if lines and lines[0].startswith("#"):
        return "\n".join([lines[0], line, *lines[1:]])
    return f"{line}\n{text}"


def is_plan_path(relative: str) -> bool:
    """`docs/<area>/plan.md` for one of the known areas."""
    return any(relative == f"docs/{area}/plan.md" for area in AREAS)


def has_open_questions(text: str) -> bool:
    """An unchecked `- [ ]` item under `## 열린 질문` (up to the next heading)."""
    inside = False
    for line in text.splitlines():
        if line.startswith("#"):
            inside = line.startswith(_OPEN_QUESTIONS_HEADING)
        elif inside and _UNCHECKED.match(line):
            return True
    return False


def detect_area(messages: Sequence[str]) -> str | None:
    """The area the most recent message that names one is about; `messages` run oldest
    first. `None` when no message names an area."""
    for message in reversed(messages):
        mentions = list(_AREA_MENTION.finditer(message))
        if mentions:
            last = mentions[-1]
            named = last["doc"] or last["name"]
            return named.lower() if named else "code"
    return None


def plan_first_enabled(project_root: Path) -> bool:
    marker = project_root / PLAN_FIRST_MARKER
    return marker.is_file() and not marker.is_symlink()

"""Plan-first development (plan.md Phase 28): code is written only after the area's
`docs/<area>/plan.md` is confirmed.

The pure reading side lives here: the plan's status line, its open questions, the area a
request is about and whether a project opted in. Gates and the `기획 확정` command build on it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

AREAS = ("linear", "code", "notion")
PLAN_FIRST_MARKER = ".piplup/plan-first"
CONFIRMED: Literal["확정"] = "확정"
DRAFT: Literal["초안"] = "초안"

_STATUS_LINE = re.compile(r"^상태:[ \t]*(\S+)[ \t]*$", re.MULTILINE)
_OPEN_QUESTIONS_HEADING = "## 열린 질문"
_QUESTION = re.compile(r"^- \[ \]\s*(?:(Q\d+)\.\s*)?(.*)$")
_RECOMMENDATION = re.compile(r"^\s+추천:\s*(.*)$")
_OPTION = re.compile(r"^\s+(\d+)[).]\s+(.+)$")
_LEADING_NUMBER = re.compile(r"^(\d+)(?!\d)")
MAX_DECISION_CHARS = 300
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


@dataclass(frozen=True)
class Question:
    """An unchecked item under `## 열린 질문`: `- [ ] Q3. 질문` plus the indented lines below it
    (`추천: ...` and numbered options like `1) 첫째`). `line` is where it starts and `end` just
    past its block."""

    number: str
    text: str
    recommendation: str | None
    line: int
    end: int
    options: tuple[str, ...] = ()


def open_questions(text: str) -> list[Question]:
    lines = text.split("\n")
    questions: list[Question] = []
    inside = False
    for index, line in enumerate(lines):
        if line.startswith("#"):
            inside = line.startswith(_OPEN_QUESTIONS_HEADING)
            continue
        match = _QUESTION.match(line) if inside else None
        if match is None:
            continue
        end = index + 1
        recommendation: str | None = None
        numbered: list[tuple[int, str]] = []
        while end < len(lines) and lines[end].startswith((" ", "\t")) and lines[end].strip():
            advice = _RECOMMENDATION.match(lines[end])
            if advice is not None and recommendation is None:
                recommendation = advice[1].strip() or None
            option = _OPTION.match(lines[end])
            if option is not None:
                numbered.append((int(option[1]), option[2].strip()))
            end += 1
        number = match[1] or f"#{len(questions) + 1}"
        questions.append(
            Question(
                number,
                match[2].strip(),
                recommendation,
                index,
                end,
                _options(numbered),
            )
        )
    return questions


def _options(numbered: list[tuple[int, str]]) -> tuple[str, ...]:
    """Options count only when they run 1, 2, 3, ... without a gap and offer a real choice."""
    if len(numbered) < 2 or [n for n, _ in numbered] != list(range(1, len(numbered) + 1)):
        return ()
    return tuple(text for _, text in numbered)


def recommended_choice(question: Question) -> int | None:
    """The option number a recommendation starts with (`추천: 2 (이유)`), if it names one."""
    if question.recommendation is None or not question.options:
        return None
    match = _LEADING_NUMBER.match(question.recommendation)
    if match is None:
        return None
    number = int(match[1])
    return number if 1 <= number <= len(question.options) else None


def recommended_decision(question: Question) -> str | None:
    """What `추천대로` records: the recommended option's wording, else the recommendation text."""
    choice = recommended_choice(question)
    if choice is not None:
        return question.options[choice - 1]
    return question.recommendation


_CHOICE = re.compile(r"^\s*([1-9]\d?)\s*$")


def parse_choice(message: str) -> int | None:
    """A message that is only an option number."""
    match = _CHOICE.match(message)
    return int(match[1]) if match else None


def has_open_questions(text: str) -> bool:
    """An unchecked `- [ ]` item under `## 열린 질문` (up to the next heading)."""
    return bool(open_questions(text))


def record_decision(text: str, question: Question, decision: str) -> str:
    """`text` with `question` checked off and a `결정:` line below its block. The decision is
    one trimmed line of at most `MAX_DECISION_CHARS` characters."""
    clean = " ".join(decision.split())
    if not clean or len(clean) > MAX_DECISION_CHARS:
        raise ValueError(f"결정은 1~{MAX_DECISION_CHARS}자의 한 줄이어야 합니다")
    lines = text.split("\n")
    if not lines[question.line].startswith("- [ ]"):
        raise ValueError("이미 정해졌거나 바뀐 질문입니다")
    lines[question.line] = lines[question.line].replace("- [ ]", "- [x]", 1)
    lines.insert(question.end, f"  결정: {clean}")
    return "\n".join(lines)


@dataclass(frozen=True)
class PlanAnswer:
    """One of the fixed answers to a plan review; `text` is the decision for `decision` and the
    option number for `choice`."""

    kind: Literal["recommended", "all_recommended", "hold", "stop", "decision", "choice"]
    text: str = ""


_ANSWER_WORDS: dict[str, Literal["recommended", "all_recommended", "hold", "stop"]] = {
    "추천대로": "recommended",
    "추천대로 해줘": "recommended",
    "나머지 추천대로": "all_recommended",
    "보류": "hold",
    "중단": "stop",
}
_DECISION = re.compile(r"^결정\s*[:：]\s*(.*)$", re.DOTALL)


def parse_answer(message: str) -> PlanAnswer | None:
    """The whole message must be one of the fixed answers; nothing else is interpreted."""
    command = message.strip()
    word = _ANSWER_WORDS.get(command)
    if word is not None:
        return PlanAnswer(word)
    decision = _DECISION.match(command)
    return PlanAnswer("decision", decision[1].strip()) if decision else None


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

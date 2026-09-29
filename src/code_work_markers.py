"""Single source of truth for text markers that mean "this is a code-work
or plan-follow request".

`RequestRouter` (outer routing) and `ExecutionWorkflow` (the workflow's own
inner gate) both need to agree on this vocabulary. Keeping two independent
copies let a phrase be added to one and forgotten in the other — exactly
what happened with "plan.md 보고 작업 진행해" — so every marker lives here
exactly once and each caller imports what it needs.
"""

from __future__ import annotations

CODE_MARKERS = (
    "수정",
    "변경",
    "고쳐",
    "구현",
    "리팩터",
    "리팩토",
    "추가",
    "테스트",
    "test",
    "pytest",
    "ruff",
    "mypy",
    "린트",
    "타입 검사",
    "코드",
    "fix",
    "implement",
    "refactor",
    "update",
    "run test",
    "run lint",
    "typecheck",
)

CODE_INTEGRATION_MARKERS = ("github 연동", "gitlab 연동", "api 연동", "연동 작업")

# Phrases that explicitly name plan.md as the specification to follow — the
# one place these strings are written out.
PLAN_FOLLOW_MARKERS = (
    "plan.md에 적은대로",
    "plan.md에 적은 대로",
    "plan.md 보고 작업 진행해",
    "plan.md 대로 작업 진행해",
)

_OTHER_PLANNING_MARKERS = (
    "작업 plan",
    "작업 계획",
    "구현 계획",
    "개발 진행",
    "계획 짜",
    "plan 짜",
    "plan 만들어",
    "설계해",
    "계획 부터 짜",
    "계획부터 짜",
)

CODE_PLANNING_MARKERS = _OTHER_PLANNING_MARKERS + PLAN_FOLLOW_MARKERS

# Vague phrases ("이 계획", "위 작업") that only mean code work in context —
# each caller decides its own gating; this only holds the shared strings.
PLAN_CONTINUATION_MARKERS = ("이 계획 진행해", "위 작업 이어서 해줘")

# "plan.md" plus a relational particle ("보고", "대로", ...) plus a work verb
# ("코딩", "개발", "진행", ...) means "implement what plan.md says" whatever
# the exact wording. Read-only verbs ("읽고 분석해") carry no work verb, so
# they stay analysis requests.
PLAN_REFERENCE_MARKERS = (
    "대로",
    "기준으로",
    "보고",
    "따라",
    "맞춰",
    "확인 후",
    "확인하고",
    "읽은 후",
)
PLAN_WORK_VERBS = ("코딩", "코드", "개발", "진행", "구현", "작업", "시작")


def is_plan_follow_work_request(text: str) -> bool:
    normalized = text.casefold()
    return (
        "plan.md" in normalized
        and any(marker in normalized for marker in PLAN_REFERENCE_MARKERS)
        and any(verb in normalized for verb in PLAN_WORK_VERBS)
    )

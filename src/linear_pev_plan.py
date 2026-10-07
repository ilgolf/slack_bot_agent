"""PEV 슬라이스 1: 계획 타입과 코드 검증기 (docs/linear/pev.md 4절).

LLM이 만든 계획(JSON 호환 dict)을 신뢰하지 않고 코드가 검증한다. 이 모듈은 Linear를
호출하지 않고, 쓰기를 실행하지 않으며, 팀 키·상태 이름을 ID로 해석하지도 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from src.linear_tools import linear_capability

MAX_STEPS = 5
MAX_WRITE_STEPS = 1  # 문서 Q2의 v1 제안값. 결정 전 임시 상한이다.
# 문자열 인자 길이 상한(임시값, plan.md Phase 29). 실제 Linear 한도는 확인하지 못했다.
MAX_TITLE_CHARS = 256
MAX_DESCRIPTION_CHARS = 10_000
MAX_NAME_CHARS = 64
_TEXT_LIMITS = {
    "title": MAX_TITLE_CHARS,
    "description": MAX_DESCRIPTION_CHARS,
    "team": MAX_NAME_CHARS,
    "state": MAX_NAME_CHARS,
}

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_STEP_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,15}$")
_ISSUE_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9]*-\d+$")

_PLAN_FIELDS = {"goal", "steps"}
_STEP_FIELDS = {"id", "operation", "kind", "args", "depends_on"}


@dataclass(frozen=True)
class StepRef:
    """앞선 단계 결과 참조. `path`는 해석기(슬라이스 3)가 읽는다."""

    step_id: str
    path: str = ""


ArgValue = str | int | StepRef


@dataclass(frozen=True)
class PlanStep:
    id: str
    operation: str
    kind: Literal["read", "write"]
    args: Mapping[str, ArgValue]
    depends_on: tuple[str, ...]


@dataclass(frozen=True)
class LinearPlan:
    goal: str
    steps: tuple[PlanStep, ...]


@dataclass(frozen=True)
class PlanRejected:
    code: str
    reason: str


@dataclass(frozen=True)
class _ArgSpec:
    required: frozenset[str]
    optional: frozenset[str]
    # 이슈 식별자(리터럴 `ENG-1` 또는 StepRef)를 받는 인자
    issue_args: frozenset[str] = frozenset()


_ARG_SPECS: dict[str, _ArgSpec] = {
    "list_teams": _ArgSpec(frozenset(), frozenset()),
    "list_issues": _ArgSpec(frozenset(), frozenset({"first"})),
    "get_issue": _ArgSpec(frozenset({"issue"}), frozenset(), frozenset({"issue"})),
    "create_issue": _ArgSpec(frozenset({"team", "title"}), frozenset({"description"})),
    "update_issue": _ArgSpec(
        frozenset({"issue"}),
        frozenset({"title", "description", "state"}),
        frozenset({"issue"}),
    ),
}


def _operation_kinds() -> dict[str, Literal["read", "write"]]:
    capability = linear_capability()
    kinds: dict[str, Literal["read", "write"]] = {}
    for operation in capability.operations:
        kinds[operation.name] = "read" if operation.kind == "query" else "write"
    return kinds


class _Reject(Exception):
    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.rejection = PlanRejected(code, reason)


def validate_plan(raw: object) -> LinearPlan | PlanRejected:
    """LLM 출력(raw)을 검증해 `LinearPlan`을 돌려주거나 `PlanRejected`를 돌려준다."""
    try:
        return _validate(raw)
    except _Reject as exc:
        return exc.rejection


def _validate(raw: object) -> LinearPlan:
    if not isinstance(raw, dict):
        raise _Reject("schema", "계획이 객체가 아닙니다")
    _check_fields(raw, _PLAN_FIELDS, _PLAN_FIELDS, "계획")
    goal = raw["goal"]
    raw_steps = raw["steps"]
    if not isinstance(goal, str) or not isinstance(raw_steps, list):
        raise _Reject("schema", "goal은 문자열, steps는 목록이어야 합니다")
    if not raw_steps:
        raise _Reject("empty_plan", "계획에 단계가 없습니다")
    if len(raw_steps) > MAX_STEPS:
        raise _Reject("too_many_steps", f"단계는 최대 {MAX_STEPS}개입니다")

    kinds = _operation_kinds()
    steps: list[PlanStep] = []
    seen: dict[str, PlanStep] = {}
    for raw_step in raw_steps:
        step = _validate_step(raw_step, kinds, seen)
        seen[step.id] = step
        steps.append(step)

    if sum(1 for step in steps if step.kind == "write") > MAX_WRITE_STEPS:
        raise _Reject("too_many_writes", f"쓰기 단계는 최대 {MAX_WRITE_STEPS}개입니다")
    return LinearPlan(goal=goal, steps=tuple(steps))


def _check_fields(
    raw: Mapping[str, object], required: set[str], allowed: set[str], label: str
) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise _Reject("schema", f"{label}에 알 수 없는 필드가 있습니다: {sorted(unknown)}")
    missing = required - set(raw)
    if missing:
        raise _Reject("schema", f"{label}에 필수 필드가 없습니다: {sorted(missing)}")


def _validate_step(
    raw: object,
    kinds: dict[str, Literal["read", "write"]],
    earlier: dict[str, PlanStep],
) -> PlanStep:
    if not isinstance(raw, dict):
        raise _Reject("schema", "단계가 객체가 아닙니다")
    _check_fields(raw, {"id", "operation", "kind", "args"}, _STEP_FIELDS, "단계")
    step_id, operation, kind = raw["id"], raw["operation"], raw["kind"]
    if not isinstance(step_id, str) or not _STEP_ID.match(step_id):
        raise _Reject("schema", "단계 id 형식이 올바르지 않습니다")
    if step_id in earlier:
        raise _Reject("dependency", f"단계 id가 중복됩니다: {step_id}")
    if not isinstance(operation, str) or operation not in _ARG_SPECS or operation not in kinds:
        raise _Reject("unknown_operation", "허용되지 않은 operation입니다")
    # kind는 LLM 선언값이므로 신뢰하지 않고 코드 표와 대조한다.
    if kind != kinds[operation]:
        raise _Reject("kind_mismatch", f"{step_id}: kind가 operation의 실제 종류와 다릅니다")

    raw_depends = raw.get("depends_on", [])
    if not isinstance(raw_depends, list) or not all(isinstance(d, str) for d in raw_depends):
        raise _Reject("schema", "depends_on은 문자열 목록이어야 합니다")
    for dep in raw_depends:
        if dep == step_id:
            raise _Reject("dependency", f"{step_id}: 자기 자신을 참조할 수 없습니다")
        if dep not in earlier:
            # 존재하지 않거나 뒤에 있는 단계. 앞 단계만 참조하므로 순환도 불가능하다.
            raise _Reject("dependency", f"{step_id}: 앞선 단계가 아닌 {dep}를 참조합니다")
    depends_on = tuple(dict.fromkeys(raw_depends))

    raw_args = raw["args"]
    if not isinstance(raw_args, dict):
        raise _Reject("schema", f"{step_id}: args가 객체가 아닙니다")
    args = _validate_args(step_id, operation, kinds[operation], raw_args, depends_on, earlier)
    return PlanStep(
        id=step_id,
        operation=operation,
        kind=kinds[operation],
        args=args,
        depends_on=depends_on,
    )


def _validate_args(
    step_id: str,
    operation: str,
    kind: Literal["read", "write"],
    raw_args: dict[str, object],
    depends_on: tuple[str, ...],
    earlier: dict[str, PlanStep],
) -> dict[str, ArgValue]:
    spec = _ARG_SPECS[operation]
    _check_args_keys(step_id, raw_args, spec)
    args: dict[str, ArgValue] = {}
    for key, value in raw_args.items():
        if key in spec.issue_args:
            args[key] = _issue_arg(step_id, kind, value, depends_on, earlier)
        elif key == "first":
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 50:
                raise _Reject("bad_args", f"{step_id}: first는 1~50 정수여야 합니다")
            args[key] = value
        else:
            args[key] = _text_arg(step_id, key, value)
    if operation == "update_issue" and not (set(args) & {"title", "description", "state"}):
        raise _Reject("bad_args", f"{step_id}: 변경할 title/description/state가 없습니다")
    return args


def _check_args_keys(step_id: str, raw_args: dict[str, object], spec: _ArgSpec) -> None:
    unknown = set(raw_args) - spec.required - spec.optional
    if unknown:
        raise _Reject("bad_args", f"{step_id}: 허용되지 않은 인자입니다: {sorted(unknown)}")
    missing = spec.required - set(raw_args)
    if missing:
        raise _Reject("bad_args", f"{step_id}: 필수 인자가 없습니다: {sorted(missing)}")


def _text_arg(step_id: str, key: str, value: object) -> str:
    if not isinstance(value, str):
        raise _Reject("bad_args", f"{step_id}: {key}는 문자열이어야 합니다")
    if key in {"title", "team", "state"} and not value.strip():
        raise _Reject("bad_args", f"{step_id}: {key}가 비어 있습니다")
    if len(value) > _TEXT_LIMITS.get(key, MAX_DESCRIPTION_CHARS):
        raise _Reject("too_long", f"{step_id}: {key}가 너무 깁니다")
    # 팀은 키, 상태는 이름으로만 지정한다(Q5 기본값: UUID 직접 입력 거부).
    if key in {"team", "state"} and _UUID.match(value.strip()):
        raise _Reject("uuid_not_allowed", f"{step_id}: {key}에 UUID를 직접 쓸 수 없습니다")
    return value


def _issue_arg(
    step_id: str,
    kind: Literal["read", "write"],
    value: object,
    depends_on: tuple[str, ...],
    earlier: dict[str, PlanStep],
) -> ArgValue:
    if isinstance(value, dict):
        if set(value) - {"ref", "path"} or "ref" not in value:
            raise _Reject("bad_args", f"{step_id}: 참조 형식이 올바르지 않습니다")
        ref, path = value["ref"], value.get("path", "")
        if not isinstance(ref, str) or not isinstance(path, str):
            raise _Reject("bad_args", f"{step_id}: 참조 형식이 올바르지 않습니다")
        if ref not in depends_on:
            raise _Reject("dependency", f"{step_id}: 참조한 {ref}가 depends_on에 없습니다")
        # 쓰기 대상은 읽기 단계의 결과에서만 가져올 수 있다(대상 추측 방지).
        if kind == "write" and earlier[ref].kind != "read":
            raise _Reject("unresolved_target", f"{step_id}: 쓰기 대상은 읽기 결과만 참조합니다")
        return StepRef(step_id=ref, path=path)
    if not isinstance(value, str) or _UUID.match(value.strip()):
        raise _Reject("uuid_not_allowed", f"{step_id}: 이슈는 식별자(ENG-123)로만 지정합니다")
    if not _ISSUE_IDENTIFIER.match(value.strip()):
        raise _Reject("unresolved_target", f"{step_id}: 이슈 식별자 형식이 아닙니다")
    return value.strip()

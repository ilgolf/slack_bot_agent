"""PEV 계획 검증기: LLM 출력을 코드가 거부하는 조건 (docs/linear/pev.md 4절)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from src.linear_pev_plan import (
    MAX_DESCRIPTION_CHARS,
    MAX_NAME_CHARS,
    MAX_STEPS,
    MAX_TITLE_CHARS,
    LinearPlan,
    PlanRejected,
    StepRef,
    validate_plan,
)

UUID = "123e4567-e89b-12d3-a456-426614174000"


def _read(step_id: str = "s1", **args: Any) -> dict[str, Any]:
    return {
        "id": step_id,
        "operation": "list_issues",
        "kind": "read",
        "args": args,
    }


def _update(step_id: str = "s2", **overrides: Any) -> dict[str, Any]:
    step: dict[str, Any] = {
        "id": step_id,
        "operation": "update_issue",
        "kind": "write",
        "args": {"issue": "ENG-1", "state": "Done"},
        "depends_on": [],
    }
    step.update(overrides)
    return step


def _plan(*steps: dict[str, Any]) -> dict[str, Any]:
    return {"goal": "g", "steps": list(steps)}


def _code(raw: object) -> str:
    result = validate_plan(raw)
    assert isinstance(result, PlanRejected), result
    return result.code


def test_valid_multi_step_plan_with_dependency() -> None:
    raw = _plan(
        _read("s1", first=5),
        _update(
            "s2",
            args={"issue": {"ref": "s1", "path": "items.0"}, "state": "Done"},
            depends_on=["s1"],
        ),
    )
    result = validate_plan(raw)
    assert isinstance(result, LinearPlan)
    assert [s.kind for s in result.steps] == ["read", "write"]
    assert result.steps[1].args["issue"] == StepRef("s1", "items.0")
    assert result.steps[1].depends_on == ("s1",)


def test_valid_create_with_team_key() -> None:
    raw = _plan(
        {
            "id": "s1",
            "operation": "create_issue",
            "kind": "write",
            "args": {"team": "ENG", "title": "t"},
        }
    )
    assert isinstance(validate_plan(raw), LinearPlan)


@pytest.mark.parametrize("raw", ["x", None, [], {"goal": "g"}, {"steps": []}])
def test_schema_failures(raw: object) -> None:
    assert _code(raw) == "schema"


def test_unknown_plan_or_step_field_rejected() -> None:
    assert _code({"goal": "g", "steps": [], "extra": 1}) == "schema"
    step = _read()
    step["query"] = "{ viewer { id } }"
    assert _code(_plan(step)) == "schema"


def test_empty_plan_is_distinct_code() -> None:
    assert _code(_plan()) == "empty_plan"


def test_unknown_operation_rejected() -> None:
    step = _read()
    step["operation"] = "delete_issue"
    assert _code(_plan(step)) == "unknown_operation"
    step["operation"] = "query { viewer { id } }"
    assert _code(_plan(step)) == "unknown_operation"


def test_declared_kind_must_match_code_table() -> None:
    step = _update(kind="read")
    assert _code(_plan(step)) == "kind_mismatch"
    step = _read()
    step["kind"] = "write"
    assert _code(_plan(step)) == "kind_mismatch"


def test_bad_args() -> None:
    assert _code(_plan(_read(first=0))) == "bad_args"
    assert _code(_plan(_read(first=51))) == "bad_args"
    assert _code(_plan(_read(first=True))) == "bad_args"
    assert _code(_plan(_read(unknown="x"))) == "bad_args"
    create = {
        "id": "s1",
        "operation": "create_issue",
        "kind": "write",
        "args": {"team": "ENG", "title": "  "},
    }
    assert _code(_plan(create)) == "bad_args"
    create["args"] = {"team": "ENG"}
    assert _code(_plan(create)) == "bad_args"
    assert _code(_plan(_update(args={"issue": "ENG-1"}))) == "bad_args"


def test_uuid_not_allowed_for_team_state_and_issue() -> None:
    create = {
        "id": "s1",
        "operation": "create_issue",
        "kind": "write",
        "args": {"team": UUID, "title": "t"},
    }
    assert _code(_plan(create)) == "uuid_not_allowed"
    assert _code(_plan(_update(args={"issue": "ENG-1", "state": UUID}))) == "uuid_not_allowed"
    assert _code(_plan(_update(args={"issue": UUID, "state": "Done"}))) == "uuid_not_allowed"


def test_dependency_violations() -> None:
    assert _code(_plan(_read("s1"), _read("s1"))) == "dependency"
    assert _code(_plan(_update("s2", depends_on=["s9"]))) == "dependency"
    assert _code(_plan(_update("s2", depends_on=["s2"]))) == "dependency"
    # 뒤에 있는 단계 참조
    first = _update("s1", depends_on=["s2"])
    assert _code(_plan(first, _read("s2"))) == "dependency"
    # 참조했지만 depends_on에 없음
    ref_args = {"issue": {"ref": "s1"}, "state": "Done"}
    assert _code(_plan(_read("s1"), _update("s2", args=ref_args))) == "dependency"


def test_write_target_cannot_be_guessed_or_taken_from_a_write() -> None:
    assert _code(_plan(_update(args={"issue": "아무거나", "state": "Done"}))) == (
        "unresolved_target"
    )
    create = {
        "id": "s1",
        "operation": "create_issue",
        "kind": "write",
        "args": {"team": "ENG", "title": "t"},
    }
    ref_args = {"issue": {"ref": "s1"}, "state": "Done"}
    # 쓰기가 둘이라 상한에 걸리지만, 그 전에 대상 추측으로 먼저 거부된다.
    assert _code(_plan(create, _update("s2", args=ref_args, depends_on=["s1"]))) == (
        "unresolved_target"
    )


def test_size_limits() -> None:
    reads = [_read(f"s{i}") for i in range(MAX_STEPS + 1)]
    assert _code(_plan(*reads)) == "too_many_steps"
    assert _code(_plan(_update("s1"), _update("s2"))) == "too_many_writes"


def test_validation_does_not_mutate_input() -> None:
    raw = _plan(_read("s1", first=5))
    snapshot = copy.deepcopy(raw)
    validate_plan(raw)
    assert raw == snapshot


def _create(**args: Any) -> dict[str, Any]:
    return {
        "id": "s1",
        "operation": "create_issue",
        "kind": "write",
        "args": {"team": "ENG", "title": "t", **args},
    }


@pytest.mark.parametrize(
    ("key", "limit"),
    [("title", MAX_TITLE_CHARS), ("description", MAX_DESCRIPTION_CHARS), ("team", MAX_NAME_CHARS)],
)
def test_text_arguments_over_their_limit_are_rejected_and_the_limit_itself_passes(
    key: str, limit: int
) -> None:
    assert isinstance(validate_plan(_plan(_create(**{key: "가" * limit}))), LinearPlan)
    assert _code(_plan(_create(**{key: "가" * (limit + 1)}))) == "too_long"


def test_a_state_name_over_the_limit_is_rejected() -> None:
    long_state = {"issue": "ENG-1", "state": "가" * (MAX_NAME_CHARS + 1)}

    assert _code(_plan(_update(args=long_state))) == "too_long"

from __future__ import annotations

import pytest

from src.code.edit_scope import (
    drop_unauthorized_protected_steps,
    edit_named_paths_in_thread,
    interpret_plan_request,
)
from src.code.plan import ExecutionPlan, ExecutionRisk, ExecutionStep


def _plan(*paths: str) -> ExecutionPlan:
    return ExecutionPlan(
        goal="g",
        project_name="demo",
        affected_files=list(paths),
        steps=[ExecutionStep("write_file", path, "x") for path in paths],
        verification_commands=[],
        risk=ExecutionRisk.MODIFY,
    )


def test_a_file_named_in_this_message_wins_over_the_thread_context() -> None:
    request = interpret_plan_request("src/b.py 고쳐줘", "스레드 맥락: src/a.py\nsrc/b.py 고쳐줘")

    assert request.explicit_target_paths == ["src/b.py"]
    assert request.target_paths == ["src/b.py"]


def test_the_thread_context_names_the_target_when_this_message_names_none() -> None:
    request = interpret_plan_request("그거 고쳐줘", "스레드 맥락: src/a.py 봐줘\n그거 고쳐줘")

    assert request.explicit_target_paths == []
    assert request.target_paths == ["src/a.py"]
    assert request.user_writable_paths == []


def test_following_plan_md_reads_it_as_evidence_but_does_not_make_it_writable() -> None:
    request = interpret_plan_request(
        "plan.md 대로 src/a.py 구현해줘", "plan.md 대로 src/a.py 구현해줘"
    )

    assert request.plan_follow_request
    assert request.plan_document_is_evidence
    assert "plan.md" in request.target_paths
    assert request.user_writable_paths == ["src/a.py"]


def test_asking_for_a_plan_to_be_written_makes_plan_md_writable() -> None:
    request = interpret_plan_request("계획 짜줘", "계획 짜줘")

    assert not request.plan_follow_request
    assert request.user_writable_paths == ["plan.md"]


@pytest.mark.parametrize(
    ("paths", "writable", "kept"),
    [
        (["src/a.py", "plan.md"], [], ["src/a.py"]),
        (["src/a.py", "plan.md"], ["plan.md"], ["src/a.py", "plan.md"]),
        (["src/a.py"], [], ["src/a.py"]),
    ],
)
def test_only_management_files_the_user_did_not_name_are_dropped(
    paths: list[str], writable: list[str], kept: list[str]
) -> None:
    plan = drop_unauthorized_protected_steps(_plan(*paths), writable)

    assert plan.affected_files == kept
    assert [step.path for step in plan.steps] == kept


def test_a_plan_of_only_unnamed_management_files_is_rejected() -> None:
    with pytest.raises(ValueError, match="plan.md"):
        drop_unauthorized_protected_steps(_plan("plan.md"), [])


def test_a_management_file_named_earlier_stays_editable_in_a_follow_up() -> None:
    named = edit_named_paths_in_thread("이어서 해줘", ["plan.md 수정해줘"])

    assert named == ["plan.md"]


def test_an_earlier_plain_file_does_not_carry_over() -> None:
    assert edit_named_paths_in_thread("이어서 해줘", ["src/a.py 수정해줘"]) == []


def test_a_plan_follow_request_keeps_plan_md_read_only_whatever_was_said_before() -> None:
    named = edit_named_paths_in_thread("plan.md 대로 구현해줘", ["plan.md 수정해줘"])

    assert "plan.md" not in named

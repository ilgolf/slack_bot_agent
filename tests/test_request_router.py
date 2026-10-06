"""Routing: fixed commands are decided in code, every other message by the configured
code-work mode (plan.md Phase 24); no classifier is consulted.
"""

from __future__ import annotations

import pytest

from src.request_router import RequestIntent, RequestRouter

# --- fixed commands: decided in code ---------------------------------------------------


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("실행", RequestIntent.CONTROL_CONFIRM),
        ("<@U1> 실행해줘", RequestIntent.CONTROL_CONFIRM),
        ("취소", RequestIntent.CONTROL_CANCEL),
        ("폐기", RequestIntent.CODE_WORK),
        ("<@U123> 폐기해줘", RequestIntent.CODE_WORK),
        ("<@U123> 이 스레드 요약해줘", RequestIntent.THREAD_SUMMARY),
        ("thread summary", RequestIntent.THREAD_SUMMARY),
        ("스레드 요약해서 md 파일로 만들어", RequestIntent.THREAD_SUMMARY),
        ("Linear 이슈 조회", RequestIntent.LINEAR_READ),
        ("Linear 팀 목록", RequestIntent.LINEAR_READ),
        ("Linear 이슈 수정 ENG-123", RequestIntent.LINEAR_MUTATION),
        ("Linear 이슈 생성\n팀 ID: x\n제목: 코드 수정", RequestIntent.LINEAR_MUTATION),
    ],
)
def test_fixed_commands_are_routed_by_code(text: str, intent: RequestIntent) -> None:
    assert RequestRouter(code_work_mode="analysis").route(text).intent is intent


def test_confirmation_carries_its_verb_and_only_execute_confirms() -> None:
    router = RequestRouter()

    assert router.route("실행").confirmation_verb == "실행"
    assert router.route("저장").intent is RequestIntent.PROJECT_ANALYSIS


@pytest.mark.parametrize(
    "text",
    ["스레드 요약 기능 지원돼?", "스레드 요약은 어떻게 동작해?", "thread summary 가능해?"],
)
def test_questions_about_thread_summary_are_not_executed_as_summaries(text: str) -> None:
    assert RequestRouter().route(text).intent is not RequestIntent.THREAD_SUMMARY


# --- every other message follows the configured mode ----------------------------------


@pytest.mark.parametrize(
    ("mode", "intent"),
    [
        ("analysis", RequestIntent.PROJECT_ANALYSIS),
        ("plan", RequestIntent.CODE_WORK),
        ("edit", RequestIntent.CODE_WORK),
    ],
)
@pytest.mark.parametrize("text", ["plan.md 부터 짜볼래?", "이 코드 어떻게 동작해?", "고마워"])
def test_the_configured_mode_decides_every_message_that_is_not_a_fixed_command(
    mode: str, intent: RequestIntent, text: str
) -> None:
    routed = RequestRouter(code_work_mode=mode).route(text)  # type: ignore[arg-type]

    assert routed.intent is intent


@pytest.mark.parametrize("mode", ["analysis", "plan", "edit"])
@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("실행", RequestIntent.CONTROL_CONFIRM),
        ("취소", RequestIntent.CONTROL_CANCEL),
        ("폐기", RequestIntent.CODE_WORK),
        ("이 스레드 요약해줘", RequestIntent.THREAD_SUMMARY),
        ("Linear 이슈 조회", RequestIntent.LINEAR_READ),
        ("Linear 이슈 수정 ENG-123", RequestIntent.LINEAR_MUTATION),
    ],
)
def test_fixed_commands_keep_their_intent_in_every_mode(
    mode: str, text: str, intent: RequestIntent
) -> None:
    assert RequestRouter(code_work_mode=mode).route(text).intent is intent  # type: ignore[arg-type]


@pytest.mark.parametrize("mode", ["analysis", "plan", "edit"])
@pytest.mark.parametrize(
    ("text", "area"),
    [
        ("기획 확정 linear", "linear"),
        ("<@U1> 기획 확정 Notion", "notion"),
        ("기획확정 code", "code"),
        ("기획 확정 linear 해줘", "linear"),
    ],
)
def test_the_plan_confirmation_is_a_fixed_command_carrying_its_area(
    mode: str, text: str, area: str
) -> None:
    routed = RequestRouter(code_work_mode=mode).route(text)  # type: ignore[arg-type]

    assert routed.intent is RequestIntent.PLAN_CONFIRM
    assert routed.area == area


@pytest.mark.parametrize("text", ["기획 확정", "기획 확정 foo", "linear 기획 확정하면 뭐가 돼?"])
def test_a_plan_confirmation_needs_an_area_and_nothing_more(text: str) -> None:
    assert RequestRouter(code_work_mode="analysis").route(text).intent is not (
        RequestIntent.PLAN_CONFIRM
    )

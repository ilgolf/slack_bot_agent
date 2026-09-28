"""Typed current-message routing avoids Linear/code-work keyword collisions."""

from __future__ import annotations

from src.request_router import RequestIntent, RequestRouter


def test_project_linear_test_request_is_code_work() -> None:
    routed = RequestRouter().route(
        "@Goodra-bot piplup-agent-v2 project에 linear 연동 부분 test 실행해볼래?"
    )

    assert routed.intent is RequestIntent.CODE_WORK


def test_integration_planning_request_is_code_work() -> None:
    routed = RequestRouter().route("Github 연동 작업 plan 짜봐", project_name="my-project")

    assert routed.intent is RequestIntent.CODE_WORK


def test_github_integration_plan_is_code_work_even_with_project_name_attached() -> None:
    routed = RequestRouter().route("piplup-agent-v2Github 연동 작업을 진행할건데 plan 부터 짜볼래?")

    assert routed.intent is RequestIntent.CODE_WORK


def test_linear_workspace_read_is_not_project_analysis() -> None:
    routed = RequestRouter().route("Linear 이슈 조회")

    assert routed.intent is RequestIntent.LINEAR_READ


def test_confirmation_and_artifact_are_typed_separately() -> None:
    router = RequestRouter()

    assert router.route("저장").intent is RequestIntent.CONTROL_CONFIRM
    assert router.route("실행").confirmation_verb == "실행"
    assert router.route("스레드 요약해서 md 파일로 만들어").intent is (
        RequestIntent.ARTIFACT_GENERATION
    )


def test_ambiguous_linear_integration_question_stays_outside_linear_api() -> None:
    routed = RequestRouter().route("Linear ticket mcp 연동 가능한지 확인해줄래?")

    assert routed.intent is RequestIntent.SYSTEM_INQUIRY

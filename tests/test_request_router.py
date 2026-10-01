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


def test_plan_file_development_request_is_code_work() -> None:
    routed = RequestRouter().route("plan.md에 적은대로 개발 진행해볼래?")

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


def test_linear_design_questions_are_inquiries_not_code_work() -> None:
    router = RequestRouter()

    assert router.route("Linear는 GraphQL 기반으로 설계한 거야?").intent is (
        RequestIntent.SYSTEM_INQUIRY
    )
    assert router.route("GraphQL 기반이야?").intent is RequestIntent.SYSTEM_INQUIRY
    assert router.route("Linear에 대한 학습은 충분해?").intent is RequestIntent.SYSTEM_INQUIRY


def test_linear_workspace_mutation_is_not_claimed_by_generic_code_verbs() -> None:
    router = RequestRouter()

    assert router.route("Linear 이슈 수정 ENG-123").intent is RequestIntent.LINEAR_MUTATION
    assert router.route("my-project Linear 연동 코드 수정해줘").intent is RequestIntent.CODE_WORK
    assert router.route("Linear 이슈 생성\n팀 ID: x\n제목: 코드 수정").intent is (
        RequestIntent.LINEAR_MUTATION
    )
    assert router.route("Linear API 연동 가능한가?").intent is RequestIntent.SYSTEM_INQUIRY
    assert router.route("Linear 연동 작업에서 이슈 생성 코드를 구현해줘").intent is (
        RequestIntent.CODE_WORK
    )


def test_question_form_plan_document_request_is_code_work() -> None:
    routed = RequestRouter().route(
        "slack_bot_agent 프로젝트에 Linear 연동 작업을 진행할건데 "
        "plan.md 에 계획 부터 짜볼래?"
    )

    assert routed.intent is RequestIntent.CODE_WORK


def test_plan_autopilot_request_is_code_work() -> None:
    routed = RequestRouter().route("@Goodra-bot my-project plan.md 기준으로 끝까지 진행해")

    assert routed.intent is RequestIntent.CODE_WORK

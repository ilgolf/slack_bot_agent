"""Typed current-message routing avoids Linear/code-work keyword collisions."""

from __future__ import annotations

import pytest

from src.request_router import RequestIntent, RequestRouter
from src.thread_context import ThreadWorkContext


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


def test_plan_file_daero_follow_phrase_is_code_work_without_thread_context() -> None:
    """"plan.md 대로 작업 진행해" names plan.md directly, just like the
    already-supported "plan.md 보고/적은대로" variants — it must not require
    prior thread context to reach CODE_WORK."""
    routed = RequestRouter().route("plan.md 대로 작업 진행해")

    assert routed.intent is RequestIntent.CODE_WORK


@pytest.mark.parametrize(
    "text",
    [
        "<@U1> plan.md 보고 코딩 진행 ㄱㄱ",
        "plan.md 보고 코딩해",
        "plan.md 읽은 후 계획대로 개발 진행",
        "plan.md 보고 개발 시작해",
    ],
)
def test_plan_follow_coding_phrases_are_code_work(text: str) -> None:
    """Free-form "plan.md 보고 <coding verb>" must not depend on one exact
    phrase — it was falling through to PROJECT_ANALYSIS in Slack."""
    assert RequestRouter().route(text).intent is RequestIntent.CODE_WORK


def test_plan_read_and_analyze_stays_project_analysis() -> None:
    routed = RequestRouter().route("slack_bot_agent 프로젝트의 plan.md 읽고 분석해")

    assert routed.intent is RequestIntent.PROJECT_ANALYSIS


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


def test_plan_follow_up_phrase_depends_on_thread_plan_context() -> None:
    router = RequestRouter()
    phrase = "이 계획 진행해"

    assert router.route(phrase).intent is RequestIntent.PROJECT_ANALYSIS
    assert router.route(
        phrase, thread_context=ThreadWorkContext(has_pending_plan=True)
    ).intent is RequestIntent.CODE_WORK


def test_generic_plan_implementation_phrase_is_code_work_without_thread_context() -> None:
    """"계획대로 구현해" is plan.md's fourth listed continuation phrase, but it
    already contains the generic "구현" marker — no thread context needed."""
    routed = RequestRouter().route("계획대로 구현해")

    assert routed.intent is RequestIntent.CODE_WORK


def test_explicit_project_name_overrides_thread_plan_context() -> None:
    router = RequestRouter()
    context = ThreadWorkContext(has_pending_plan=True, project_name="project-a")

    assert router.route(
        "이 계획 진행해", project_name="project-a", thread_context=context
    ).intent is RequestIntent.CODE_WORK
    assert router.route(
        "이 계획 진행해", project_name="project-b", thread_context=context
    ).intent is RequestIntent.PROJECT_ANALYSIS


def test_question_form_plan_document_request_is_code_work() -> None:
    routed = RequestRouter().route(
        "slack_bot_agent 프로젝트에 Linear 연동 작업을 진행할건데 "
        "plan.md 에 계획 부터 짜볼래?"
    )

    assert routed.intent is RequestIntent.CODE_WORK


class _FakeIntentClassifier:
    def __init__(self, intent: RequestIntent) -> None:
        self.intent = intent

    def classify(self, text: str, thread_context: ThreadWorkContext | None) -> RequestIntent:
        return self.intent


def test_unmatched_request_is_code_work_when_classifier_says_so() -> None:
    router = RequestRouter(intent_classifier=_FakeIntentClassifier(RequestIntent.CODE_WORK))

    routed = router.route("계획서 내용대로 슬슬 달려보자구")

    assert routed.intent is RequestIntent.CODE_WORK


def test_unmatched_request_stays_analysis_when_classifier_says_analysis() -> None:
    router = RequestRouter(intent_classifier=_FakeIntentClassifier(RequestIntent.PROJECT_ANALYSIS))

    routed = router.route("계획서 내용대로 슬슬 달려보자구")

    assert routed.intent is RequestIntent.PROJECT_ANALYSIS


@pytest.mark.parametrize(
    "intent",
    [
        RequestIntent.LINEAR_MUTATION,
        RequestIntent.CONTROL_CONFIRM,
        RequestIntent.ARTIFACT_GENERATION,
    ],
)
def test_classifier_cannot_produce_intents_beyond_code_work(intent: RequestIntent) -> None:
    router = RequestRouter(intent_classifier=_FakeIntentClassifier(intent))

    routed = router.route("계획서 내용대로 슬슬 달려보자구")

    assert routed.intent is RequestIntent.PROJECT_ANALYSIS


class _FailingIntentClassifier:
    def classify(self, text: str, thread_context: ThreadWorkContext | None) -> RequestIntent:
        raise RuntimeError("LLM unavailable")


def test_classifier_failure_falls_back_to_project_analysis() -> None:
    router = RequestRouter(intent_classifier=_FailingIntentClassifier())

    routed = router.route("계획서 내용대로 슬슬 달려보자구")

    assert routed.intent is RequestIntent.PROJECT_ANALYSIS


class _CountingIntentClassifier:
    def __init__(self) -> None:
        self.calls = 0

    def classify(self, text: str, thread_context: ThreadWorkContext | None) -> RequestIntent:
        self.calls += 1
        return RequestIntent.CODE_WORK


@pytest.mark.parametrize(
    "text",
    [
        "실행",
        "취소",
        "Linear 이슈 조회",
        "plan.md 보고 코딩 진행 ㄱㄱ",
        "src/app.py 수정해줘",
    ],
)
def test_classifier_is_not_called_when_rules_already_decide(text: str) -> None:
    classifier = _CountingIntentClassifier()

    RequestRouter(intent_classifier=classifier).route(text)

    assert classifier.calls == 0

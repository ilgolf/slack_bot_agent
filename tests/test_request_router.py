"""Routing: only fixed commands are decided in code; every other message is the
classifier's call, and an unavailable or unsure classifier means a read-only answer
(plan.md Phase 20, section C).
"""

from __future__ import annotations

import pytest

from src.request_router import RequestIntent, RequestRouter
from src.thread_context import ThreadWorkContext


class _Classifier:
    def __init__(self, intent: RequestIntent = RequestIntent.PROJECT_ANALYSIS) -> None:
        self.intent = intent
        self.calls: list[tuple[str, ThreadWorkContext | None]] = []

    def classify(self, text: str, thread_context: ThreadWorkContext | None) -> RequestIntent:
        self.calls.append((text, thread_context))
        return self.intent


class _FailingClassifier:
    def classify(self, text: str, thread_context: ThreadWorkContext | None) -> RequestIntent:
        raise RuntimeError("LLM unavailable")


# --- fixed commands: decided in code, the classifier is never asked --------------------


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
def test_fixed_commands_are_routed_without_asking_the_classifier(
    text: str, intent: RequestIntent
) -> None:
    classifier = _Classifier(RequestIntent.CODE_WORK)

    assert RequestRouter(intent_classifier=classifier).route(text).intent is intent
    assert classifier.calls == []


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


# --- everything else is the classifier's call -------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "my-project 로그인 버그 수정해줘",
        "piplup-agent-v2 project에 linear 연동 부분 test 실행해볼래?",
        "Github 연동 작업 plan 짜봐",
        "plan.md 보고 코딩 진행 ㄱㄱ",
        "my-project plan.md 기준으로 끝까지 진행해",
        "my-project Linear 연동 코드 수정해줘",
        "이 계획 진행해",
        "계획서 내용대로 슬슬 달려보자구",
    ],
)
def test_code_work_is_whatever_the_classifier_says(text: str) -> None:
    code = RequestRouter(intent_classifier=_Classifier(RequestIntent.CODE_WORK)).route(text)
    other = RequestRouter(intent_classifier=_Classifier(RequestIntent.PROJECT_ANALYSIS)).route(text)

    assert (code.intent, code.llm_classified) == (RequestIntent.CODE_WORK, True)
    assert other.intent is RequestIntent.PROJECT_ANALYSIS


@pytest.mark.parametrize(
    "text",
    [
        "thread 내용 기반으로 정리해서 ticket을 만들 수 있는 상황임?",
        "Linear API 연동 가능한가?",
        "Linear ticket mcp 연동 가능한지 확인해줄래?",
        "README 요약해줘",
        "이메일 목록 정리해줘",
    ],
)
def test_questions_and_analysis_requests_have_no_special_intent(text: str) -> None:
    classifier = _Classifier(RequestIntent.PROJECT_ANALYSIS)

    assert RequestRouter(intent_classifier=classifier).route(text).intent is (
        RequestIntent.PROJECT_ANALYSIS
    )
    assert [call[0] for call in classifier.calls] == [text]


def test_the_classifier_sees_the_thread_work_state() -> None:
    classifier = _Classifier()
    context = ThreadWorkContext(has_pending_plan=True, project_name="project-a")

    RequestRouter(intent_classifier=classifier).route("이 계획 진행해", thread_context=context)

    assert classifier.calls == [("이 계획 진행해", context)]


@pytest.mark.parametrize(
    "intent",
    [RequestIntent.LINEAR_MUTATION, RequestIntent.CONTROL_CONFIRM, RequestIntent.THREAD_SUMMARY],
)
def test_classifier_cannot_produce_intents_beyond_code_work(intent: RequestIntent) -> None:
    router = RequestRouter(intent_classifier=_Classifier(intent))

    assert router.route("계획서 내용대로 슬슬 달려보자구").intent is RequestIntent.PROJECT_ANALYSIS


def test_without_a_working_classifier_the_request_is_a_read_only_answer() -> None:
    text = "my-project 로그인 버그 수정해줘"

    assert RequestRouter().route(text).intent is RequestIntent.PROJECT_ANALYSIS
    assert RequestRouter(intent_classifier=_FailingClassifier()).route(text).intent is (
        RequestIntent.PROJECT_ANALYSIS
    )


# --- Phase 24: the configured mode, not a classifier, decides every other message ------


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
    classifier = _Classifier(RequestIntent.PROJECT_ANALYSIS)

    routed = RequestRouter(intent_classifier=classifier, code_work_mode=mode).route(text)  # type: ignore[arg-type]

    assert routed.intent is intent
    assert classifier.calls == []


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

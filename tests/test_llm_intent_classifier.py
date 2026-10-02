"""LLM-backed intent classifier: parses a chat model's answer into a RequestIntent."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage

from src.agent import FakeAnalysisAgent
from src.code_agent_analysis import CodeAgentAnalysisAgent, RunnerError, RunnerTimeout
from src.langchain_agent import LangChainAnalysisAgent
from src.llm_intent_classifier import (
    LlmIntentClassifier,
    RunnerIntentClassifier,
    build_intent_classifier,
)
from src.project_resolver import ProjectResolver
from src.request_router import RequestIntent, RequestRouter
from src.thread_context import ThreadWorkContext


class _FakeChatModel:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.inputs: list[list[BaseMessage]] = []

    def invoke(self, input: list[BaseMessage]) -> AIMessage:
        self.inputs.append(input)
        return AIMessage(content=self.answer)


def test_model_answer_code_work_is_parsed_into_code_work_intent() -> None:
    classifier = LlmIntentClassifier(_FakeChatModel("code_work"))

    intent = classifier.classify("계획서 내용대로 슬슬 달려보자구", None)

    assert intent is RequestIntent.CODE_WORK


def test_unrecognized_model_answer_is_project_analysis() -> None:
    classifier = LlmIntentClassifier(_FakeChatModel("잘 모르겠습니다. 코드를 짜야 할 수도 있어요."))

    intent = classifier.classify("계획서 내용대로 슬슬 달려보자구", None)

    assert intent is RequestIntent.PROJECT_ANALYSIS


def test_pending_plan_in_thread_context_is_included_in_prompt() -> None:
    model = _FakeChatModel("code_work")
    context = ThreadWorkContext(has_pending_plan=True, last_intent_was_code_work=True)

    LlmIntentClassifier(model).classify("이거 진행해", context)

    prompt = str(model.inputs[0][0].content)
    assert "대기 중인 실행 계획" in prompt


def test_classifier_is_built_from_the_agents_chat_model(tmp_path: Path) -> None:
    agent = LangChainAnalysisAgent(
        chat_model=_FakeChatModel("code_work"),
        project_resolver=ProjectResolver(root=tmp_path),
    )

    classifier = build_intent_classifier(agent)

    assert classifier is not None
    assert classifier.classify("이거 진행해", None) is RequestIntent.CODE_WORK


def test_no_classifier_is_built_for_an_agent_without_a_chat_model() -> None:
    assert build_intent_classifier(FakeAnalysisAgent()) is None


# --- Phase 18, E: classifier backed by a code agent's text-only runner ------------------


class _FakeTextRunner:
    name = "fake_runner"

    def __init__(self, answer: str = "", error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.prompts: list[str] = []
        self.timeouts: list[float] = []

    def complete(self, prompt: str, *, timeout_seconds: float) -> str:
        self.prompts.append(prompt)
        self.timeouts.append(timeout_seconds)
        if self.error is not None:
            raise self.error
        return self.answer


@pytest.mark.parametrize("answer", ["code_work", " CODE_WORK\n", "`code_work`", '"code_work".'])
def test_runner_answer_naming_code_work_is_code_work(answer: str) -> None:
    runner = _FakeTextRunner(answer)

    intent = RunnerIntentClassifier(runner).classify("README를 읽고 개선해줘", None)

    assert intent is RequestIntent.CODE_WORK
    assert "README를 읽고 개선해줘" in runner.prompts[0]
    assert runner.timeouts == [60.0]


@pytest.mark.parametrize(
    "answer",
    [
        "project_analysis",
        "",
        "잘 모르겠습니다",
        "I think code_work",
        "code_work or project_analysis",
    ],
)
def test_any_other_runner_answer_is_project_analysis(answer: str) -> None:
    intent = RunnerIntentClassifier(_FakeTextRunner(answer)).classify("이거 봐줘", None)

    assert intent is RequestIntent.PROJECT_ANALYSIS


@pytest.mark.parametrize("error", [RunnerError("SECRET"), RunnerTimeout(), RuntimeError("boom")])
def test_runner_failures_fall_back_to_project_analysis_without_raising(error: Exception) -> None:
    intent = RunnerIntentClassifier(_FakeTextRunner(error=error)).classify("개선해줘", None)

    assert intent is RequestIntent.PROJECT_ANALYSIS


def test_runner_classifier_includes_the_thread_state_note() -> None:
    runner = _FakeTextRunner("code_work")
    context = ThreadWorkContext(has_pending_plan=True, last_intent_was_code_work=True)

    RunnerIntentClassifier(runner).classify("이거 진행해", context)

    assert "대기 중인 실행 계획" in runner.prompts[0]


def test_build_intent_classifier_uses_the_runner_of_a_code_agent(tmp_path: Path) -> None:
    runner: Any = _FakeTextRunner()  # only `complete` matters to the classifier
    agent = CodeAgentAnalysisAgent(runner=runner, project_resolver=ProjectResolver(root=tmp_path))

    assert isinstance(build_intent_classifier(agent), RunnerIntentClassifier)
    assert build_intent_classifier(FakeAnalysisAgent()) is None


class _CountingClassifier:
    def __init__(self, intent: RequestIntent) -> None:
        self.intent = intent
        self.calls = 0

    def classify(self, text: str, thread_context: ThreadWorkContext | None) -> RequestIntent:
        self.calls += 1
        return self.intent


def test_the_router_asks_the_classifier_only_for_requests_its_keywords_do_not_settle() -> None:
    classifier = _CountingClassifier(RequestIntent.CODE_WORK)
    router = RequestRouter(intent_classifier=classifier)

    settled = router.route("src/calc.py에 subtract 함수를 추가해줘")
    assert (settled.intent, classifier.calls) == (RequestIntent.CODE_WORK, 0)

    unsettled = router.route("README를 읽고 개선해줘")
    assert (unsettled.intent, classifier.calls) == (RequestIntent.CODE_WORK, 1)
    assert unsettled.llm_classified is True

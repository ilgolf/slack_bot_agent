"""LLM-backed intent classifier: parses a chat model's answer into a RequestIntent."""

from __future__ import annotations

from pathlib import Path

from langchain_core.messages import AIMessage, BaseMessage

from src.agent import FakeAnalysisAgent
from src.langchain_agent import LangChainAnalysisAgent
from src.llm_intent_classifier import LlmIntentClassifier, build_intent_classifier
from src.project_resolver import ProjectResolver
from src.request_router import RequestIntent
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

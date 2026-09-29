"""LLM fallback for requests the deterministic router could not classify."""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from src.langchain_agent import ChatModel
from src.message_text import content_text
from src.request_router import IntentClassifier, RequestIntent
from src.thread_context import ThreadWorkContext

_PROMPT = (
    "Classify the Slack request as exactly one of: code_work, project_analysis.\n"
    "code_work means the user wants code written, changed, or run.\n"
    "Answer with the label only.\n\nRequest:\n"
)


class LlmIntentClassifier:
    def __init__(self, model: ChatModel) -> None:
        self._model = model

    def classify(self, text: str, thread_context: ThreadWorkContext | None) -> RequestIntent:
        prompt = _PROMPT + text + _thread_state_note(thread_context)
        answer = self._model.invoke([HumanMessage(content=prompt)])
        if content_text(answer.content).strip().casefold() == RequestIntent.CODE_WORK:
            return RequestIntent.CODE_WORK
        return RequestIntent.PROJECT_ANALYSIS


def _thread_state_note(thread_context: ThreadWorkContext | None) -> str:
    if thread_context is None:
        return ""
    if thread_context.has_pending_plan:
        return "\n\nThread state: 대기 중인 실행 계획이 있습니다."
    if thread_context.last_intent_was_code_work:
        return "\n\nThread state: 이 스레드는 직전에 코드 작업 중이었습니다."
    return ""


def build_intent_classifier(agent: object) -> IntentClassifier | None:
    """Reuse the analysis agent's chat model; agents without one (the fake) get none."""
    chat_model = getattr(agent, "chat_model", None)
    return LlmIntentClassifier(chat_model) if chat_model is not None else None

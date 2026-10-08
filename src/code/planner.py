"""LLM-backed `NextActionPlanner` for `CodeAgentLoop`.

Turns a LangChain chat model's tool-call response into a typed `ToolCall`, and
(once implemented) a tool-call-free reply into a `PlannerFinal`. Kept separate
from `code_agent_loop.py` so the loop itself stays LLM-agnostic.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage

from src.code.loop import AgentPlan, PlannerFinal, ToolCall
from src.code.tool_registry import ToolOutcome
from src.core.message_text import content_text

_FENCED_JSON = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)
_DEFERRED_CALL_MESSAGE = (
    "이 도구 호출은 이번 턴에 실행하지 않았습니다. "
    "다음 턴에 현재 결과를 바탕으로 하나씩 다시 요청하세요."
)


class ChatModel(Protocol):
    """The slice of a LangChain chat model's interface this planner needs —
    mirrors `src.code.langchain_agent.ChatModel`, kept local to avoid coupling this
    module to the analysis agent module."""

    def invoke(self, input: list[BaseMessage]) -> Any: ...


@dataclass
class LangChainNextActionPlanner:
    """Keeps its own conversation across calls: each `next_action` appends the
    prior outcome as a `ToolMessage` before asking the model for the next
    step, so the model sees a normal tool-calling transcript."""

    chat_model: ChatModel
    _messages: list[BaseMessage] = field(default_factory=list, init=False, repr=False)
    _pending_call_ids: list[str] = field(default_factory=list, init=False, repr=False)

    def next_action(
        self, plan: AgentPlan, outcomes: list[ToolOutcome]
    ) -> ToolCall | PlannerFinal | None:
        if not self._messages:
            self._messages.append(HumanMessage(content=plan.goal))
        elif self._pending_call_ids and outcomes:
            call_id = self._pending_call_ids.pop(0)
            tool_message = ToolMessage(content=outcomes[-1].safe_message, tool_call_id=call_id)
            self._messages.append(tool_message)

        response = self.chat_model.invoke(self._messages)
        self._messages.append(response)
        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            return self._parse_final(response.content)

        call = tool_calls[0]
        for deferred in tool_calls[1:]:
            self._messages.append(
                ToolMessage(content=_DEFERRED_CALL_MESSAGE, tool_call_id=deferred["id"])
            )
        self._pending_call_ids = [call["id"]]
        return ToolCall(name=call["name"], args=dict(call["args"]))

    @staticmethod
    def _parse_final(content: object) -> PlannerFinal:
        text = content_text(content).strip()
        fenced = _FENCED_JSON.fullmatch(text)
        if fenced:
            text = fenced.group(1)
        try:
            data = json.loads(text.replace(" ", " "))
            return PlannerFinal(
                summary=data["summary"],
                findings=tuple(data.get("findings", [])),
                limitations=tuple(data.get("limitations", [])),
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            # `None` would mean "nothing more to add" and let the loop finish
            # with its own generic success text — a parse failure must stay
            # visible as a limitation instead.
            return PlannerFinal(
                summary="모델 응답을 해석할 수 없습니다.",
                limitations=("모델 응답이 예상한 JSON 형식이 아닙니다.",),
            )

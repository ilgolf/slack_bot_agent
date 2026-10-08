"""LangChainNextActionPlanner: turns a chat model's response into the typed
actions `CodeAgentLoop` understands — no real LLM call, only fake models."""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from src.code.loop import AgentPlan, PlannerFinal, ToolCall
from src.code.planner import LangChainNextActionPlanner
from src.code.tool_registry import ToolOutcome


class FakeToolCallModel:
    def invoke(self, input: list[BaseMessage]) -> AIMessage:
        del input
        return AIMessage(
            content="",
            tool_calls=[
                {"name": "read_file", "args": {"relative_path": "app.py"}, "id": "call_1"}
            ],
        )


class FakeFinalModel:
    def __init__(self, content: str) -> None:
        self.content = content

    def invoke(self, input: list[BaseMessage]) -> AIMessage:
        del input
        return AIMessage(content=self.content, tool_calls=[])


def _plan() -> AgentPlan:
    return AgentPlan(goal="구조 확인", selected_project="demo", steps=[])


def test_planner_converts_tool_call_response_to_tool_call() -> None:
    planner = LangChainNextActionPlanner(chat_model=FakeToolCallModel())

    action = planner.next_action(_plan(), [])

    assert action == ToolCall(name="read_file", args={"relative_path": "app.py"})


def test_planner_returns_planner_final_when_no_tool_call() -> None:
    content = '{"summary": "요약", "findings": ["f1"], "limitations": ["l1"]}'
    planner = LangChainNextActionPlanner(chat_model=FakeFinalModel(content))

    action = planner.next_action(_plan(), [])

    assert action == PlannerFinal(summary="요약", findings=("f1",), limitations=("l1",))


def test_planner_returns_an_honest_planner_final_when_reply_is_not_valid_json() -> None:
    """An unparsable final reply must not be silently treated as `None` (=
    "planner has nothing more to add") — that would let the loop declare
    success with a leftover generic message instead of surfacing the
    failure."""
    planner = LangChainNextActionPlanner(chat_model=FakeFinalModel("이건 JSON이 아니에요"))

    action = planner.next_action(_plan(), [])

    assert isinstance(action, PlannerFinal)
    assert action.limitations


class RecordingSequencedModel:
    def __init__(self, responses: list[AIMessage]) -> None:
        self.responses = responses
        self.received_inputs: list[list[BaseMessage]] = []

    def invoke(self, input: list[BaseMessage]) -> AIMessage:
        self.received_inputs.append(list(input))
        return self.responses[len(self.received_inputs) - 1]


def test_planner_adopts_first_tool_call_and_defers_the_rest() -> None:
    first_response = AIMessage(
        content="",
        tool_calls=[
            {"name": "read_file", "args": {"relative_path": "a.py"}, "id": "call_a"},
            {"name": "read_file", "args": {"relative_path": "b.py"}, "id": "call_b"},
        ],
    )
    second_response = AIMessage(content="", tool_calls=[])
    model = RecordingSequencedModel([first_response, second_response])
    planner = LangChainNextActionPlanner(chat_model=model)

    first_action = planner.next_action(_plan(), [])
    assert first_action == ToolCall(name="read_file", args={"relative_path": "a.py"})

    planner.next_action(_plan(), [ToolOutcome("ok", "content-a")])

    second_input = model.received_inputs[1]
    tool_messages = [m for m in second_input if isinstance(m, ToolMessage)]
    deferred = next(m for m in tool_messages if m.tool_call_id == "call_b")
    assert "다시 요청" in deferred.content


def test_planner_forwards_previous_outcome_as_tool_message() -> None:
    first_response = AIMessage(
        content="",
        tool_calls=[
            {"name": "read_file", "args": {"relative_path": "app.py"}, "id": "call_1"}
        ],
    )
    second_response = AIMessage(content="", tool_calls=[])
    model = RecordingSequencedModel([first_response, second_response])
    planner = LangChainNextActionPlanner(chat_model=model)

    planner.next_action(_plan(), [])
    planner.next_action(_plan(), [ToolOutcome("ok", "file contents here")])

    second_input = model.received_inputs[1]
    tool_messages = [m for m in second_input if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert tool_messages[0].tool_call_id == "call_1"
    assert tool_messages[0].content == "file contents here"

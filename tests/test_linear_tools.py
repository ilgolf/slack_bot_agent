"""Only fixed, typed Linear operations are available to the Slack workflow."""

from __future__ import annotations

from typing import Any

import pytest

from src.linear_tools import (
    CREATE_ISSUE,
    GET_ISSUE,
    LIST_ISSUES,
    LIST_TEAMS,
    UPDATE_ISSUE,
    LinearTools,
)


class RecordingClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def query(self, **kwargs: object) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _issue() -> dict[str, Any]:
    return {
        "id": "uuid-1",
        "identifier": "ENG-123",
        "title": "Fix login",
        "description": "details",
        "url": "https://linear.app/acme/issue/ENG-123",
        "team": {"id": "team-1", "name": "Engineering", "key": "ENG"},
        "state": {"id": "state-1", "name": "Todo"},
    }


def test_read_operations_use_fixed_documents_and_parse_results() -> None:
    client = RecordingClient(
        [
            {"teams": {"nodes": [{"id": "team-1", "name": "Engineering", "key": "ENG"}]}},
            {"issues": {"nodes": [_issue()]}},
            {"issue": _issue()},
        ]
    )
    tools = LinearTools(client)  # type: ignore[arg-type]

    assert tools.list_teams()[0].key == "ENG"
    assert tools.list_issues()[0].identifier == "ENG-123"
    assert tools.get_issue("ENG-123").id == "uuid-1"  # type: ignore[union-attr]
    assert [call["document"] for call in client.calls] == [LIST_TEAMS, LIST_ISSUES, GET_ISSUE]
    assert client.calls[1]["variables"] == {"first": 20}
    assert client.calls[2]["variables"] == {"identifier": "ENG-123"}


def test_mutations_allow_only_documented_fields() -> None:
    client = RecordingClient(
        [
            {"issueCreate": {"success": True, "issue": _issue()}},
            {"issueUpdate": {"success": True, "issue": _issue()}},
        ]
    )
    tools = LinearTools(client)  # type: ignore[arg-type]

    tools.create_issue(team_id="team-1", title="Fix login", description="details")
    tools.update_issue(issue_id="uuid-1", title="Updated", state_id="state-2")

    assert [call["document"] for call in client.calls] == [CREATE_ISSUE, UPDATE_ISSUE]
    assert client.calls[0]["variables"] == {
        "input": {"teamId": "team-1", "title": "Fix login", "description": "details"}
    }
    assert client.calls[1]["variables"] == {
        "id": "uuid-1",
        "input": {"title": "Updated", "stateId": "state-2"},
    }


def test_mutation_validates_required_values() -> None:
    tools = LinearTools(RecordingClient([]))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="팀 ID"):
        tools.create_issue(team_id="", title="title")
    with pytest.raises(ValueError, match="중 하나"):
        tools.update_issue(issue_id="uuid")
    with pytest.raises(ValueError, match="1~50"):
        tools.list_issues(first=51)

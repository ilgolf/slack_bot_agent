"""Fixed, typed Linear operations; no arbitrary GraphQL is exposed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.linear_client import LinearApiError, LinearClient

LIST_TEAMS = """
query ListTeams { teams { nodes { id name key } } }
"""
LIST_ISSUES = """
query ListIssues($first: Int!) {
  issues(first: $first) {
    nodes { id identifier title description url state { id name } team { id name key } }
  }
}
"""
GET_ISSUE = """
query GetIssue($identifier: String!) {
  issue(id: $identifier) {
    id identifier title description url state { id name } team { id name key }
  }
}
"""
CREATE_ISSUE = """
mutation CreateIssue($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue { id identifier title url }
  }
}
"""
UPDATE_ISSUE = """
mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) {
    success
    issue { id identifier title url }
  }
}
"""


@dataclass(frozen=True)
class LinearTeam:
    id: str
    name: str
    key: str


@dataclass(frozen=True)
class LinearIssue:
    id: str
    identifier: str
    title: str
    description: str | None
    url: str | None
    team_id: str | None
    team_name: str | None
    state_id: str | None
    state_name: str | None


class LinearTools:
    """Expose only the read and mutation operations approved for Goodra-bot."""

    def __init__(self, client: LinearClient) -> None:
        self.client = client

    def list_teams(self) -> list[LinearTeam]:
        data = self.client.query(operation="list_teams", document=LIST_TEAMS)
        return [_to_team(item) for item in _nodes(data, "teams")]

    def list_issues(self, *, first: int = 20) -> list[LinearIssue]:
        if not 1 <= first <= 50:
            raise ValueError("조회할 이슈 수는 1~50개여야 합니다")
        data = self.client.query(
            operation="list_issues", document=LIST_ISSUES, variables={"first": first}
        )
        return [_to_issue(item) for item in _nodes(data, "issues")]

    def get_issue(self, identifier: str) -> LinearIssue | None:
        if not identifier.strip():
            raise ValueError("이슈 식별자가 필요합니다")
        data = self.client.query(
            operation="get_issue", document=GET_ISSUE, variables={"identifier": identifier}
        )
        raw_issue = data.get("issue")
        if raw_issue is None:
            return None
        if not isinstance(raw_issue, dict):
            raise LinearApiError("Linear API 응답 형식이 올바르지 않습니다.")
        return _to_issue(raw_issue)

    def create_issue(
        self, *, team_id: str, title: str, description: str | None = None
    ) -> LinearIssue:
        if not team_id.strip() or not title.strip():
            raise ValueError("팀 ID와 제목은 필수입니다")
        input_data: dict[str, str] = {"teamId": team_id, "title": title}
        if description:
            input_data["description"] = description
        data = self.client.query(
            operation="create_issue", document=CREATE_ISSUE, variables={"input": input_data}
        )
        return _mutation_issue(data, "issueCreate")

    def update_issue(
        self,
        *,
        issue_id: str,
        title: str | None = None,
        description: str | None = None,
        state_id: str | None = None,
    ) -> LinearIssue:
        if not issue_id.strip():
            raise ValueError("이슈 ID가 필요합니다")
        input_data = {
            key: value
            for key, value in {
                "title": title,
                "description": description,
                "stateId": state_id,
            }.items()
            if value is not None
        }
        if not input_data:
            raise ValueError("제목, 설명, 상태 ID 중 하나를 변경해야 합니다")
        data = self.client.query(
            operation="update_issue",
            document=UPDATE_ISSUE,
            variables={"id": issue_id, "input": input_data},
        )
        return _mutation_issue(data, "issueUpdate")


def _nodes(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    connection = data.get(key)
    if not isinstance(connection, dict) or not isinstance(connection.get("nodes"), list):
        raise LinearApiError("Linear API 응답 형식이 올바르지 않습니다.")
    nodes = connection["nodes"]
    if not all(isinstance(item, dict) for item in nodes):
        raise LinearApiError("Linear API 응답 형식이 올바르지 않습니다.")
    return [item for item in nodes if isinstance(item, dict)]


def _to_team(value: dict[str, Any]) -> LinearTeam:
    try:
        return LinearTeam(id=str(value["id"]), name=str(value["name"]), key=str(value["key"]))
    except (KeyError, TypeError) as exc:
        raise LinearApiError("Linear API 응답 형식이 올바르지 않습니다.") from exc


def _to_issue(value: dict[str, Any]) -> LinearIssue:
    try:
        team = value.get("team") or {}
        state = value.get("state") or {}
        if not isinstance(team, dict) or not isinstance(state, dict):
            raise TypeError
        return LinearIssue(
            id=str(value["id"]),
            identifier=str(value["identifier"]),
            title=str(value["title"]),
            description=_optional_text(value.get("description")),
            url=_optional_text(value.get("url")),
            team_id=_optional_text(team.get("id")),
            team_name=_optional_text(team.get("name")),
            state_id=_optional_text(state.get("id")),
            state_name=_optional_text(state.get("name")),
        )
    except (KeyError, TypeError) as exc:
        raise LinearApiError("Linear API 응답 형식이 올바르지 않습니다.") from exc


def _optional_text(value: object) -> str | None:
    return str(value) if value is not None else None


def _mutation_issue(data: dict[str, Any], key: str) -> LinearIssue:
    result = data.get(key)
    if not isinstance(result, dict) or result.get("success") is not True:
        raise LinearApiError("Linear API가 변경을 완료하지 못했습니다.")
    issue = result.get("issue")
    if not isinstance(issue, dict):
        raise LinearApiError("Linear API 응답 형식이 올바르지 않습니다.")
    return _to_issue(issue)

"""Linear Slack routing stays outside project analysis and confirms mutations."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx

from src.config import Settings
from src.linear_client import LinearClient
from src.linear_tools import LinearTools
from src.linear_workflow import LinearIntegrationWorkflow, PendingLinearActionStore
from src.thread_context import ThreadContextStore


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


def _workflow(
    *, store: PendingLinearActionStore | None = None
) -> tuple[LinearIntegrationWorkflow, list[str]]:
    operations: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        if "GetIssue" in body:
            operations.append("get_issue")
            return httpx.Response(200, json={"data": {"issue": _issue()}})
        if "CreateIssue" in body:
            operations.append("create_issue")
            return httpx.Response(
                200,
                json={"data": {"issueCreate": {"success": True, "issue": _issue()}}},
            )
        if "UpdateIssue" in body:
            operations.append("update_issue")
            return httpx.Response(
                200,
                json={"data": {"issueUpdate": {"success": True, "issue": _issue()}}},
            )
        operations.append("list_teams")
        return httpx.Response(
            200,
            json={
                "data": {
                    "teams": {"nodes": [{"id": "team-1", "name": "Engineering", "key": "ENG"}]}
                }
            },
        )

    client = LinearClient(
        api_key="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return (
        LinearIntegrationWorkflow(
            settings=Settings(linear_api_key="secret"),
            tools=LinearTools(client),
            action_store=store,
        ),
        operations,
    )


def _process(workflow: LinearIntegrationWorkflow, text: str) -> str | None:
    return workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text=text,
        thread_context=ThreadContextStore(root="/tmp/linear-context-unused"),
        agent=object(),
    )


def test_missing_key_returns_setup_without_requesting_linear() -> None:
    workflow = LinearIntegrationWorkflow(settings=Settings(linear_api_key=None))

    response = _process(workflow, "Linear 연결 상태 확인")

    assert response is not None
    assert "LINEAR_API_KEY" in response


def test_read_request_runs_without_project_analysis() -> None:
    workflow, operations = _workflow()

    response = _process(workflow, "Linear 팀 조회")

    assert response is not None
    assert "Engineering" in response
    assert operations == ["list_teams"]


def test_create_is_previewed_then_executed_once() -> None:
    workflow, operations = _workflow()

    preview = _process(
        workflow,
        "Linear 이슈 생성\n팀 ID: team-1\n제목: 로그인 오류 수정\n설명: 재현 가능",
    )

    assert preview is not None
    assert "초안" in preview
    assert operations == []
    result = _process(workflow, "실행")
    assert result is not None
    assert "ENG-123" in result
    assert operations == ["create_issue"]
    assert _process(workflow, "실행") is None
    assert operations == ["create_issue"]


def test_update_reads_target_for_preview_then_mutates_on_confirmation() -> None:
    workflow, operations = _workflow()

    preview = _process(workflow, "Linear 이슈 수정 ENG-123\n상태 ID: state-2")

    assert preview is not None
    assert "이슈 수정" in preview
    assert operations == ["get_issue"]
    _process(workflow, "실행")
    assert operations == ["get_issue", "update_issue"]


def test_cancel_and_expiry_never_call_mutation() -> None:
    workflow, operations = _workflow()
    _process(workflow, "Linear 이슈 생성\n팀 ID: team-1\n제목: 로그인 오류 수정")
    assert "취소" in (_process(workflow, "취소") or "")
    assert operations == []

    expired_workflow, expired_operations = _workflow(
        store=PendingLinearActionStore(ttl=timedelta(seconds=-1))
    )
    _process(expired_workflow, "Linear 이슈 생성\n팀 ID: team-1\n제목: 로그인 오류 수정")
    assert "만료" in (_process(expired_workflow, "실행") or "")
    assert expired_operations == []

"""`PR`·`실행`·`취소`가 PR 초안으로 가는 길 (plan.md Phase 33 D)."""

from __future__ import annotations

from pathlib import Path

from src.code.github import GhCli, GhResult
from src.code.pr_workflow import PullRequestFacts, PullRequestWorkflow
from src.code.workflow import ExecutionWorkflow
from src.code.workspace import GitCommands
from src.core.config import Settings
from src.core.project_resolver import ProjectResolver
from src.linear.workflow import LinearIntegrationWorkflow
from src.slack.request_coordinator import RequestCoordinator
from src.slack.request_router import RequestRouter
from src.slack.thread_context import ThreadContextStore

_FACTS = PullRequestFacts("bot/C1-1-0", "main", 1, "bot: 수정", ["a.py"])


class Calls:
    def __init__(self) -> None:
        self.log: list[str] = []

    def git(self, argv: list[str]) -> str:
        self.log.append("git " + " ".join(argv[5:]))
        return ""

    def gh(self, argv: list[str], cwd: Path) -> GhResult:
        self.log.append("gh " + " ".join(argv[1:3]))
        return GhResult(0, "https://github.com/acme/repo/pull/7\n", "")


def _coordinator(tmp_path: Path, calls: Calls) -> tuple[RequestCoordinator, PullRequestWorkflow]:
    pr = PullRequestWorkflow(
        facts_source=lambda channel_id, thread_ts: _FACTS,
        worktree_of=lambda channel_id, thread_ts: Path("/wt"),
        git=GitCommands(calls.git),
        gh=GhCli(calls.gh),
    )
    coordinator = RequestCoordinator(
        router=RequestRouter(),
        execution_workflow=ExecutionWorkflow(project_resolver=ProjectResolver(root=tmp_path)),
        linear_workflow=LinearIntegrationWorkflow(settings=Settings(linear_api_key=None)),
        pull_request_workflow=pr,
    )
    return coordinator, pr


def _send(coordinator: RequestCoordinator, tmp_path: Path, text: str, user: str | None) -> str:
    _, response = coordinator.process(
        channel_id="C1",
        thread_ts="1.0",
        text=text,
        thread_context=ThreadContextStore(root=tmp_path / "context"),
        agent=object(),  # type: ignore[arg-type]
        user_id=user,
    )
    return response


def test_the_pr_command_previews_a_draft_for_the_user_who_sent_it(tmp_path: Path) -> None:
    calls = Calls()
    coordinator, pr = _coordinator(tmp_path, calls)

    reply = _send(coordinator, tmp_path, "<@UBOT> PR 제목: 수정", "U42")

    assert "PR 초안" in reply
    assert "수정" in reply
    pending = pr.pending("C1", "1.0")
    assert pending is not None
    assert pending.requester == "U42"
    assert calls.log == []


def test_execute_from_the_requester_pushes_and_opens_the_pull_request(tmp_path: Path) -> None:
    calls = Calls()
    coordinator, _ = _coordinator(tmp_path, calls)
    _send(coordinator, tmp_path, "PR", "U42")

    reply = _send(coordinator, tmp_path, "실행", "U42")

    assert "https://github.com/acme/repo/pull/7" in reply
    assert calls.log == ["git push origin bot/C1-1-0", "gh pr create"]


def test_execute_from_someone_else_does_not_send_the_draft(tmp_path: Path) -> None:
    calls = Calls()
    coordinator, pr = _coordinator(tmp_path, calls)
    _send(coordinator, tmp_path, "PR", "U42")

    reply = _send(coordinator, tmp_path, "실행", "U99")

    assert "요청한 사람만" in reply
    assert calls.log == []
    assert pr.has_pending("C1", "1.0")


def test_cancel_drops_the_pull_request_draft_without_touching_git(tmp_path: Path) -> None:
    calls = Calls()
    coordinator, pr = _coordinator(tmp_path, calls)
    _send(coordinator, tmp_path, "PR", "U42")

    reply = _send(coordinator, tmp_path, "취소", "U42")

    assert "취소" in reply
    assert not pr.has_pending("C1", "1.0")
    assert calls.log == []


def test_execute_does_not_guess_when_a_code_plan_is_pending_too(tmp_path: Path) -> None:
    calls = Calls()
    coordinator, pr = _coordinator(tmp_path, calls)
    _send(coordinator, tmp_path, "PR", "U42")
    coordinator.execution_workflow.has_pending = lambda channel_id, thread_ts: True  # type: ignore[method-assign]

    reply = _send(coordinator, tmp_path, "실행", "U42")

    assert "여러 개" in reply
    assert calls.log == []
    assert pr.has_pending("C1", "1.0")


def test_without_a_pull_request_workflow_the_command_says_it_is_not_set_up(
    tmp_path: Path,
) -> None:
    coordinator, _ = _coordinator(tmp_path, Calls())
    coordinator.pull_request_workflow = None

    assert "설정되지 않았습니다" in _send(coordinator, tmp_path, "PR", "U42")

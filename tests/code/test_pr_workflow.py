"""`PR` 명령의 미리보기 (plan.md Phase 33 D). 사실은 주입한다."""

from __future__ import annotations

from pathlib import Path

from src.code.github import GhCli, GhResult
from src.code.pr_workflow import PullRequestFacts, PullRequestWorkflow
from src.code.workspace import GitCommands

_FACTS = PullRequestFacts(
    branch="bot/C1-1.0",
    base="main",
    commit_count=2,
    first_commit_subject="bot: 로그인 수정",
    changed_files=["src/a.py", "tests/test_a.py"],
)


class Calls:
    """git과 gh 호출을 순서대로 기록한다."""

    def __init__(self, gh_url: str = "https://github.com/acme/repo/pull/7") -> None:
        self.log: list[str] = []
        self._gh_url = gh_url

    def git_runner(self, argv: list[str]) -> str:
        self.log.append("git " + " ".join(argv[5:]))
        return ""

    def gh_runner(self, argv: list[str], cwd: Path) -> GhResult:
        self.log.append("gh " + " ".join(argv[1:3]))
        return GhResult(0, self._gh_url + "\n", "")


def _workflow(calls: Calls, *, facts: PullRequestFacts | None = _FACTS) -> PullRequestWorkflow:
    return PullRequestWorkflow(
        facts_source=lambda channel_id, thread_ts: facts,
        worktree_of=lambda channel_id, thread_ts: Path("/wt"),
        git=GitCommands(calls.git_runner),
        gh=GhCli(calls.gh_runner),
    )


def test_the_preview_shows_branch_base_commits_title_and_files_and_keeps_a_draft() -> None:
    workflow = _workflow(Calls())

    reply = workflow.preview("C1", "1.0", "PR 제목: 로그인 오류 수정")

    assert "`bot/C1-1.0`" in reply
    assert "`main`" in reply
    assert "커밋 2개" in reply
    assert "로그인 오류 수정" in reply
    assert "`src/a.py`" in reply
    assert "원격과 다를 수 있" in reply
    assert "`실행`" in reply
    assert workflow.has_pending("C1", "1.0")


def test_without_a_thread_branch_to_send_no_draft_is_kept() -> None:
    workflow = _workflow(Calls(), facts=None)

    reply = workflow.preview("C1", "1.0", "PR")

    assert "올릴 작업 브랜치가 없습니다" in reply
    assert not workflow.has_pending("C1", "1.0")


def test_the_draft_remembers_who_asked_for_it() -> None:
    workflow = _workflow(Calls())

    workflow.preview("C1", "1.0", "PR", requester="U42")

    pending = workflow.pending("C1", "1.0")
    assert pending is not None
    assert pending.requester == "U42"


def test_someone_other_than_the_requester_cannot_send_it_and_the_draft_stays() -> None:
    calls = Calls()
    workflow = _workflow(calls)
    workflow.preview("C1", "1.0", "PR", requester="U42")

    reply = workflow.confirm("C1", "1.0", user_id="U99")

    assert reply is not None
    assert "요청한 사람만" in reply
    assert calls.log == []
    assert workflow.has_pending("C1", "1.0")


def test_an_unknown_requester_can_never_send_it() -> None:
    calls = Calls()
    workflow = _workflow(calls)
    workflow.preview("C1", "1.0", "PR")

    reply = workflow.confirm("C1", "1.0", user_id=None)

    assert reply is not None
    assert "요청한 사람만" in reply
    assert calls.log == []


def test_the_requester_pushes_the_branch_then_opens_the_pull_request_exactly_once() -> None:
    calls = Calls()
    workflow = _workflow(calls)
    workflow.preview("C1", "1.0", "PR 제목: 로그인 오류 수정", requester="U42")

    reply = workflow.confirm("C1", "1.0", user_id="U42")
    again = workflow.confirm("C1", "1.0", user_id="U42")

    assert reply is not None
    assert "https://github.com/acme/repo/pull/7" in reply
    assert calls.log == ["git push origin bot/C1-1.0", "gh pr create"]
    assert again is None
    assert not workflow.has_pending("C1", "1.0")

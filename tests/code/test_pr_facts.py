"""스레드 브랜치의 사실을 git·gh에서 읽는다 (plan.md Phase 33 D). 둘 다 fake다."""

from __future__ import annotations

from pathlib import Path

from src.code.github import GhCli, GhResult
from src.code.pr_facts import thread_pull_request_facts
from src.code.pr_workflow import PullRequestFacts
from src.code.workspace import GitCommands, ThreadWorkspaces


class FakeGit:
    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> str:
        subcommand_args = argv[5:]
        self.calls.append(subcommand_args)
        return self.answers[" ".join(subcommand_args)]


def _gh(branch: str = "main") -> GhCli:
    return GhCli(lambda argv, cwd: GhResult(0, branch + "\n", ""))


def test_facts_come_from_the_thread_worktree_against_the_default_branch(tmp_path: Path) -> None:
    worktree = tmp_path / "project" / "C1-1-0"
    worktree.mkdir(parents=True)
    git = FakeGit(
        {
            "rev-list --count main..HEAD": "2\n",
            "log --reverse --format=%s main..HEAD": "bot: 로그인 수정\nbot: 테스트 추가\n",
            "diff --name-only main...HEAD": "src/a.py\ntests/test_a.py\n",
        }
    )
    workspaces = ThreadWorkspaces(tmp_path, GitCommands(git))

    facts = thread_pull_request_facts(workspaces, _gh())("C1", "1.0")

    assert facts == PullRequestFacts(
        branch="bot/C1-1-0",
        base="main",
        commit_count=2,
        first_commit_subject="bot: 로그인 수정",
        changed_files=["src/a.py", "tests/test_a.py"],
    )


def test_a_thread_without_a_worktree_has_no_facts(tmp_path: Path) -> None:
    git = FakeGit({})
    workspaces = ThreadWorkspaces(tmp_path, GitCommands(git))

    assert thread_pull_request_facts(workspaces, _gh())("C1", "1.0") is None
    assert git.calls == []

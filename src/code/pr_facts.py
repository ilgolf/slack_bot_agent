"""스레드 브랜치의 PR 사실을 읽는다 (plan.md Phase 33). 읽기 전용이며 기준은 로컬 기본 브랜치다."""

from __future__ import annotations

from src.code.github import GhCli
from src.code.pr_workflow import FactsSource, PullRequestFacts
from src.code.workspace import ThreadWorkspaces


def thread_pull_request_facts(workspaces: ThreadWorkspaces, gh: GhCli) -> FactsSource:
    def read(channel_id: str, thread_ts: str) -> PullRequestFacts | None:
        worktree = workspaces.thread_worktree(channel_id, thread_ts)
        if worktree is None:
            return None
        git = workspaces.git
        base = gh.default_branch(worktree)
        revisions = f"{base}..HEAD"
        count = int(git(worktree, "rev-list", "--count", revisions).strip())
        subjects = git(worktree, "log", "--reverse", "--format=%s", revisions).splitlines()
        files = git(worktree, "diff", "--name-only", f"{base}...HEAD").splitlines()
        return PullRequestFacts(
            branch=workspaces.branch_name(channel_id, thread_ts),
            base=base,
            commit_count=count,
            first_commit_subject=subjects[0] if subjects else "",
            changed_files=files,
        )

    return read

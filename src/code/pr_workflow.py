"""`PR` 명령: 미리보기를 보여 주고 `실행` 뒤에만 보낸다 (plan.md Phase 33, docs/code/plan.md)."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock

from src.code.github import GhCli, PullRequestError
from src.code.pull_request import PullRequestDraft, build_pull_request
from src.code.workspace import GitCommands


@dataclass(frozen=True)
class PullRequestFacts:
    """스레드 브랜치의 사실. 로컬 기본 브랜치(`base`) 기준으로 센 값이다."""

    branch: str
    base: str
    commit_count: int
    first_commit_subject: str
    changed_files: list[str]


@dataclass(frozen=True)
class PendingPullRequest:
    facts: PullRequestFacts
    draft: PullRequestDraft
    requester: str | None
    created_at: datetime


FactsSource = Callable[[str, str], PullRequestFacts | None]
WorktreeOf = Callable[[str, str], Path | None]


class PullRequestWorkflow:
    def __init__(
        self,
        *,
        facts_source: FactsSource,
        worktree_of: WorktreeOf,
        git: GitCommands,
        gh: GhCli,
        ttl: timedelta = timedelta(minutes=15),
    ) -> None:
        self._ttl = ttl
        self._facts_source = facts_source
        self._worktree_of = worktree_of
        self._git = git
        self._gh = gh
        self._lock = Lock()
        self._pending: dict[tuple[str, str], PendingPullRequest] = {}

    def preview(
        self, channel_id: str, thread_ts: str, command: str, *, requester: str | None = None
    ) -> str:
        try:
            facts = self._facts_source(channel_id, thread_ts)
        except PullRequestError as exc:
            return f"PR 초안을 만들지 못했습니다. {exc}"
        except (RuntimeError, ValueError, subprocess.SubprocessError):
            return "PR 초안을 만들지 못했습니다. git으로 브랜치 정보를 읽지 못했습니다."
        if facts is None:
            return "올릴 작업 브랜치가 없습니다. 이 스레드에서 먼저 코드 작업을 실행해 주세요."
        if facts.commit_count == 0:
            return f"올릴 커밋이 없습니다. `{facts.base}`와 같은 상태입니다."
        draft = build_pull_request(
            command,
            commit_subject=facts.first_commit_subject,
            changed_files=facts.changed_files,
        )
        with self._lock:
            self._pending[(channel_id, thread_ts)] = PendingPullRequest(
                facts, draft, requester, datetime.now(UTC)
            )
        return _render_preview(facts, draft)

    def confirm(self, channel_id: str, thread_ts: str, *, user_id: str | None) -> str | None:
        """`실행`: 요청자 본인만 보낼 수 있다. 초안이 없으면 `None`(다른 흐름이 처리)."""
        with self._lock:
            pending = self._pending.get((channel_id, thread_ts))
        if pending is None:
            return None
        if self._expired(pending):
            with self._lock:
                self._pending.pop((channel_id, thread_ts), None)
            return "보류된 PR 초안이 만료되었습니다. `PR`을 다시 보내 주세요. 푸시하지 않았습니다."
        if user_id is None or user_id != pending.requester:
            return "PR을 요청한 사람만 `실행`할 수 있습니다. 초안은 그대로 남겨 두었습니다."
        with self._lock:
            self._pending.pop((channel_id, thread_ts), None)
        worktree = self._worktree_of(channel_id, thread_ts)
        if worktree is None:
            return "올릴 작업 브랜치가 없습니다. 이 스레드에서 먼저 코드 작업을 실행해 주세요."
        try:
            self._git(worktree, "push", "origin", pending.facts.branch)
        except (RuntimeError, ValueError, subprocess.SubprocessError):
            return (
                "❌ 브랜치 푸시에 실패해 PR을 만들지 않았습니다. 자동 재시도하지 않았습니다. "
                "원인을 확인한 뒤 `PR`을 다시 보내 주세요."
            )
        try:
            url = self._gh.create_pull_request(
                worktree, head=pending.facts.branch, base=pending.facts.base, draft=pending.draft
            )
        except PullRequestError as exc:
            existing = f"\n기존 PR: {exc.url}" if exc.url else ""
            return (
                f"⚠️ 브랜치 푸시는 완료했지만 PR은 만들지 못했습니다. {exc}{existing}\n"
                "자동 재시도하지 않았습니다."
            )
        return f"✅ PR을 만들었습니다: {url}"

    def cancel(self, channel_id: str, thread_ts: str) -> str | None:
        """`취소`: 초안을 버린다. 초안이 없으면 `None`(다른 흐름이 처리)."""
        if self.pending(channel_id, thread_ts) is None:
            return None
        with self._lock:
            self._pending.pop((channel_id, thread_ts), None)
        return "보류된 PR 초안을 취소했습니다. 푸시하지 않았습니다."

    def pending(self, channel_id: str, thread_ts: str) -> PendingPullRequest | None:
        with self._lock:
            pending = self._pending.get((channel_id, thread_ts))
            if pending is not None and self._expired(pending):
                self._pending.pop((channel_id, thread_ts), None)
                return None
            return pending

    def _expired(self, pending: PendingPullRequest) -> bool:
        return datetime.now(UTC) - pending.created_at > self._ttl

    def has_pending(self, channel_id: str, thread_ts: str) -> bool:
        return self.pending(channel_id, thread_ts) is not None


def _render_preview(facts: PullRequestFacts, draft: PullRequestDraft) -> str:
    return (
        "PR 초안을 만들었습니다. 아직 푸시하거나 PR을 만들지 않았습니다.\n"
        f"브랜치: `{facts.branch}` → base `{facts.base}` (커밋 {facts.commit_count}개)\n"
        f"제목: {draft.title}\n\n{draft.body}\n\n"
        "커밋 수와 파일은 로컬 기본 브랜치 기준이라 원격과 다를 수 있습니다.\n"
        "확인한 뒤 같은 스레드에 `실행`이라고 보내면 푸시하고 PR을 만듭니다."
    )

"""`PR` 명령: 미리보기를 보여 주고 `실행` 뒤에만 보낸다 (plan.md Phase 33, docs/code/plan.md)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock

from src.code.pull_request import PullRequestDraft, build_pull_request


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


FactsSource = Callable[[str, str], PullRequestFacts | None]


class PullRequestWorkflow:
    def __init__(self, *, facts_source: FactsSource) -> None:
        self._facts_source = facts_source
        self._lock = Lock()
        self._pending: dict[tuple[str, str], PendingPullRequest] = {}

    def preview(
        self, channel_id: str, thread_ts: str, command: str, *, requester: str | None = None
    ) -> str:
        facts = self._facts_source(channel_id, thread_ts)
        if facts is None:
            return "올릴 작업 브랜치가 없습니다. 이 스레드에서 먼저 코드 작업을 실행해 주세요."
        draft = build_pull_request(
            command,
            commit_subject=facts.first_commit_subject,
            changed_files=facts.changed_files,
        )
        with self._lock:
            self._pending[(channel_id, thread_ts)] = PendingPullRequest(facts, draft, requester)
        return _render_preview(facts, draft)

    def pending(self, channel_id: str, thread_ts: str) -> PendingPullRequest | None:
        with self._lock:
            return self._pending.get((channel_id, thread_ts))

    def has_pending(self, channel_id: str, thread_ts: str) -> bool:
        with self._lock:
            return (channel_id, thread_ts) in self._pending


def _render_preview(facts: PullRequestFacts, draft: PullRequestDraft) -> str:
    return (
        "PR 초안을 만들었습니다. 아직 푸시하거나 PR을 만들지 않았습니다.\n"
        f"브랜치: `{facts.branch}` → base `{facts.base}` (커밋 {facts.commit_count}개)\n"
        f"제목: {draft.title}\n\n{draft.body}\n\n"
        "커밋 수와 파일은 로컬 기본 브랜치 기준이라 원격과 다를 수 있습니다.\n"
        "확인한 뒤 같은 스레드에 `실행`이라고 보내면 푸시하고 PR을 만듭니다."
    )

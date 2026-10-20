"""The plan-review dialogue behind `기획 확정 <영역>` (plan.md Phases 28 and 31).

Plain code, no agent: it asks one open question at a time, the person decides, and each
decision is written to the area's plan and committed. Only this code sets a plan to `확정`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from src.code.workspace import ThreadWorkspaces, WorkspaceError
from src.core.plan_first import (
    CONFIRMED,
    MAX_DECISION_CHARS,
    PlanAnswer,
    Question,
    has_open_questions,
    open_questions,
    parse_status,
    plan_first_enabled,
    plan_path,
    recommended_choice,
    recommended_decision,
    record_decision,
    with_status,
)

_PLAN_REVIEW_TTL_SECONDS = 30 * 60


_NO_PLAN_REVIEW = (
    "진행 중인 기획 검토가 없습니다. `기획 확정 <영역>`을 보내 열린 질문 확인을 시작해 주세요."
)


@dataclass
class _PlanReview:
    """An open plan review in one thread: which area, when it started, what was put on hold."""

    area: str
    started: float
    held: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class _Recorded:
    text: str
    decision: str
    error: str | None = None


def _question_key(question: Question) -> str:
    return f"{question.number}\n{question.text}"


class PlanReviewDialogue:
    """Open plan reviews per Slack thread, backed by the thread's worktree."""

    def __init__(self, workspaces: ThreadWorkspaces | None) -> None:
        self.workspaces = workspaces
        self._plan_reviews: dict[tuple[str, str], _PlanReview] = {}

    def confirm_plan(self, channel_id: str, thread_ts: str, area: str) -> str:
        """The user's `기획 확정 <영역>` (plan.md Phase 28): only this code, never the agent,
        sets an area's plan to `확정`, and only when it has no open questions left."""
        worktree = (
            self.workspaces.thread_worktree(channel_id, thread_ts)
            if self.workspaces is not None
            else None
        )
        if worktree is None:
            return (
                "확정할 기획이 없습니다. 먼저 `개발 진행해`로 이 스레드에서 기획을 쓰게 해 주세요."
            )
        if not plan_first_enabled(worktree):
            return "이 프로젝트는 기획 우선 모드가 아닙니다 (`.piplup/plan-first`가 없습니다)."
        relative = f"docs/{area}/plan.md"
        try:
            text = plan_path(worktree, area).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return f"`{relative}`가 없어 확정할 수 없습니다. 먼저 기획을 쓰게 해 주세요."
        if parse_status(text) == CONFIRMED:
            return f"`{relative}`는 이미 확정되어 있습니다."
        if has_open_questions(text):
            self._plan_reviews[(channel_id, thread_ts)] = _PlanReview(area, time.monotonic())
            return self._next_question(channel_id, thread_ts, worktree, area, text)
        assert self.workspaces is not None
        try:
            plan_path(worktree, area).write_text(with_status(text, CONFIRMED), encoding="utf-8")
            self.workspaces.commit(worktree, [relative], f"기획 확정: {area}")
        except (OSError, WorkspaceError):
            return f"❌ `{relative}`를 확정하지 못했습니다. 파일을 확인해 주세요."
        return (
            f"✅ `{relative}`를 확정했습니다. 이제 같은 스레드에서 개발을 요청하면 "
            "기획의 슬라이스를 구현합니다."
        )

    def has_plan_review(self, channel_id: str, thread_ts: str) -> bool:
        review = self._plan_reviews.get((channel_id, thread_ts))
        return review is not None and time.monotonic() - review.started <= _PLAN_REVIEW_TTL_SECONDS

    def answer_plan_review(self, channel_id: str, thread_ts: str, answer: PlanAnswer) -> str:
        """An answer to the plan review `기획 확정 <영역>` started (plan.md Phase 31). The
        dialogue is plain code: it asks, the person decides, the decision is recorded."""
        key = (channel_id, thread_ts)
        review = self._plan_reviews.get(key)
        worktree = (
            self.workspaces.thread_worktree(channel_id, thread_ts)
            if self.workspaces is not None
            else None
        )
        if review is None or worktree is None:
            return _NO_PLAN_REVIEW
        if time.monotonic() - review.started > _PLAN_REVIEW_TTL_SECONDS:
            del self._plan_reviews[key]
            return _NO_PLAN_REVIEW
        try:
            text = plan_path(worktree, review.area).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            del self._plan_reviews[key]
            return _NO_PLAN_REVIEW
        pending = [q for q in open_questions(text) if _question_key(q) not in review.held]
        if answer.kind == "stop":
            del self._plan_reviews[key]
            left = len(open_questions(text))
            return (
                f"기획 검토를 중단했습니다. 열린 질문 {left}개가 남아 있습니다. "
                f"`기획 확정 {review.area}`를 보내면 이어서 묻습니다."
            )
        if not pending:
            return self._next_question(channel_id, thread_ts, worktree, review.area, text)
        current = pending[0]
        if answer.kind == "hold":
            review.held.add(_question_key(current))
            prefix = f"{current.number}를 보류했습니다.\n\n"
            return self._next_question(channel_id, thread_ts, worktree, review.area, text, prefix)
        if answer.kind == "all_recommended":
            return self._record_all_recommended(channel_id, thread_ts, worktree, review, pending)
        if answer.kind == "recommended" and recommended_decision(current) is None:
            return (
                f"{current.number}에는 추천이 없어 `추천대로`를 쓸 수 없습니다. "
                "`결정: <내용>`으로 답하거나 `보류`해 주세요."
            )
        if answer.kind == "choice":
            if not current.options:
                return (
                    f"{current.number}에는 선택지가 없습니다. `결정: <내용>`으로 답하거나 "
                    "`보류`해 주세요."
                )
            number = int(answer.text)
            if not 1 <= number <= len(current.options):
                return f"{current.number}의 선택지는 1~{len(current.options)}번입니다."
            decision: str | None = current.options[number - 1]
        elif answer.kind == "recommended":
            decision = recommended_decision(current)
        else:
            decision = answer.text
        recorded = self._record_decision(worktree, review.area, text, current, decision or "")
        if recorded.error is not None:
            return recorded.error
        prefix = f"✅ {current.number} 결정을 기록했습니다: {recorded.decision}\n\n"
        return self._next_question(
            channel_id, thread_ts, worktree, review.area, recorded.text, prefix
        )

    def _record_all_recommended(
        self,
        channel_id: str,
        thread_ts: str,
        worktree: Path,
        review: _PlanReview,
        pending: list[Question],
    ) -> str:
        missing = [q.number for q in pending if recommended_decision(q) is None]
        if missing:
            return (
                f"{', '.join(missing)}에는 추천이 없어 `나머지 추천대로`를 쓸 수 없습니다. "
                "`결정: <내용>`으로 답하거나 `보류`해 주세요."
            )
        numbers = [q.number for q in pending]
        text = plan_path(worktree, review.area).read_text(encoding="utf-8")
        for _ in numbers:
            current = next(q for q in open_questions(text) if _question_key(q) not in review.held)
            recorded = self._record_decision(
                worktree, review.area, text, current, recommended_decision(current) or ""
            )
            if recorded.error is not None:
                return recorded.error
            text = recorded.text
        prefix = f"✅ 남은 질문 {len(numbers)}개를 추천대로 기록했습니다: {', '.join(numbers)}\n\n"
        return self._next_question(channel_id, thread_ts, worktree, review.area, text, prefix)

    def _record_decision(
        self, worktree: Path, area: str, text: str, question: Question, decision: str
    ) -> _Recorded:
        assert self.workspaces is not None
        relative = f"docs/{area}/plan.md"
        try:
            updated = record_decision(text, question, decision)
        except ValueError:
            return _Recorded(
                text, decision, f"결정은 1~{MAX_DECISION_CHARS}자(300자 이내)의 한 줄이어야 합니다."
            )
        try:
            plan_path(worktree, area).write_text(updated, encoding="utf-8")
            self.workspaces.commit(worktree, [relative], f"기획 결정: {area} {question.number}")
        except (OSError, WorkspaceError):
            return _Recorded(text, decision, f"❌ `{relative}`에 결정을 기록하지 못했습니다.")
        return _Recorded(updated, " ".join(decision.split()))

    def _next_question(
        self,
        channel_id: str,
        thread_ts: str,
        worktree: Path,
        area: str,
        text: str,
        prefix: str = "",
    ) -> str:
        key = (channel_id, thread_ts)
        review = self._plan_reviews.get(key)
        held = review.held if review is not None else set()
        open_now = open_questions(text)
        pending = [q for q in open_now if _question_key(q) not in held]
        if pending:
            question = pending[0]
            advice = (
                f"추천: {question.recommendation}"
                if question.recommendation
                else "추천: 없음 — `결정: <내용>`으로 답해 주세요."
            )
            recommended = recommended_choice(question)
            options = "".join(
                f"{number}) {option}{' (추천)' if number == recommended else ''}\n"
                for number, option in enumerate(question.options, start=1)
            )
            how = (
                "번호만 보내면 됩니다. 그 밖의 답: "
                if question.options
                else "답하는 법: `추천대로` · "
            ) + "`결정: <내용>` · `보류` · `나머지 추천대로` · `중단`"
            return (
                f"{prefix}📝 `docs/{area}/plan.md` 열린 질문 {len(open_now)}개 중 하나입니다.\n"
                f"*{question.number}.* {question.text}\n{options}{advice}\n\n{how}"
            )
        self._plan_reviews.pop(key, None)
        if open_now:
            return (
                f"{prefix}남은 질문 {len(open_now)}개가 모두 보류 상태입니다. 결정한 뒤 "
                f"`기획 확정 {area}`를 다시 보내면 이어서 묻습니다."
            )
        return (
            f"{prefix}모든 열린 질문이 정해졌습니다. `기획 확정 {area}`를 다시 보내면 "
            "기획을 확정합니다."
        )

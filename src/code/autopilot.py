"""Autopilot: `끝까지 진행` works through every unchecked item of a project's plan.md.

Each item is one ordinary `process()` run, auto-executed; the item is checked off only after
that run succeeded, and a `취소` stops the loop after the current item.
"""

from __future__ import annotations

from collections.abc import Callable

from src.code.plan import _UNCHECKED_ITEM, next_unchecked_item
from src.code.verifier import ExecutionResult, _diff_summary, _render_check
from src.core.project_resolver import ProjectResolver
from src.core.run_state import CodeWorkState, CodeWorkStateStore
from src.slack.thread_context import ThreadContextStore


class Autopilot:
    """Per-thread autopilot runs; `process` is the workflow entry that runs one item."""

    def __init__(
        self,
        *,
        project_resolver: ProjectResolver,
        state_store: CodeWorkStateStore,
        last_results: dict[tuple[str, str], ExecutionResult],
        max_items: int,
        process: Callable[..., str | None],
    ) -> None:
        self._project_resolver = project_resolver
        self._state_store = state_store
        self._last_results = last_results
        self.max_items = max_items
        self._process = process
        self._active: set[tuple[str, str]] = set()
        self._cancelled: set[tuple[str, str]] = set()

    def is_active(self, channel_id: str, thread_ts: str) -> bool:
        return (channel_id, thread_ts) in self._active

    def cancel(self, channel_id: str, thread_ts: str) -> None:
        self._cancelled.add((channel_id, thread_ts))

    def run(
        self,
        project_name: str,
        channel_id: str,
        thread_ts: str,
        thread_context: ThreadContextStore,
        agent: object,
        on_progress: Callable[[str], None] | None = None,
    ) -> str | None:
        """Plan and execute every unchecked plan.md item without confirmation,
        checking each off only after its verification succeeded."""
        key = (channel_id, thread_ts)
        self._active.add(key)
        try:
            return self._loop(project_name, key, thread_context, agent, on_progress)
        finally:
            self._active.discard(key)
            self._cancelled.discard(key)

    def _loop(
        self,
        project_name: str,
        key: tuple[str, str],
        thread_context: ThreadContextStore,
        agent: object,
        on_progress: Callable[[str], None] | None,
    ) -> str | None:
        channel_id, thread_ts = key
        plan_file = self._project_resolver.resolve(project_name) / "plan.md"
        total = len(_UNCHECKED_ITEM.findall(plan_file.read_text()))
        result: str | None = None
        completed = 0
        summary_lines: list[str] = []
        all_diffs: list[str] = []
        while (item := next_unchecked_item(plan_file.read_text())) is not None:
            if completed >= self.max_items:
                remaining = len(_UNCHECKED_ITEM.findall(plan_file.read_text()))
                return (
                    f"⛔ 자동 진행 중단: 최대 {self.max_items}개 항목까지만 "
                    f"진행합니다 ({completed}개 완료, 남은 항목 {remaining}개)\n{result}"
                )
            result = self._process(
                channel_id=channel_id,
                thread_ts=thread_ts,
                text=f"{project_name} plan.md 기준으로 다음 항목을 진행해: {item}",
                thread_context=thread_context,
                agent=agent,
                auto_execute=True,
            )
            state = self._state_store.state(channel_id=channel_id, thread_ts=thread_ts)
            if state is not CodeWorkState.SUCCEEDED:
                return f"⛔ 자동 진행 중단 ({completed}개 완료): `{item}` 실패\n{result}"
            plan_file.write_text(plan_file.read_text().replace(f"- [ ] {item}", f"- [x] {item}", 1))
            completed += 1
            finished = self._last_results.get(key)
            files = ", ".join(
                f"`{path}`" for path in dict.fromkeys(finished.changed_files if finished else [])
            )
            unverified = " (검증 없음)" if finished is None or not finished.checks else ""
            summary_lines.append(f"- ✅ {item}: {files}{unverified}")
            if finished is not None:
                all_diffs.extend(finished.diffs)
                summary_lines.extend(f"  {_render_check(check)}" for check in finished.checks)
            if on_progress is not None:
                on_progress(f"🔄 자동 진행 {completed}/{total} 완료")
            if key in self._cancelled:
                remaining = len(_UNCHECKED_ITEM.findall(plan_file.read_text()))
                return f"⛔ 자동 진행 취소 ({completed}개 완료, 남은 항목 {remaining}개)\n{result}"
        return (
            f"✅ 전체 완료 ({completed}/{total}개 항목)\n"
            + "\n".join(summary_lines)
            + f"\nDiff 요약: {_diff_summary(all_diffs)}"
        )

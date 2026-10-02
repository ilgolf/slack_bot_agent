"""Per-thread git worktrees for code work (plan.md Phase 17).

Every git call goes through `GitCommands`: a fixed subcommand allowlist, hooks
disabled, no shell. The bot never pushes, merges, resets or checks out.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

GitRunner = Callable[[list[str]], str]

_ALLOWED_SUBCOMMANDS = {"rev-parse", "status", "worktree", "branch", "add", "commit", "diff"}
_ALLOWED_WORKTREE_ACTIONS = {"add", "remove", "list", "prune"}
BOT_BRANCH_PREFIX = "bot/"
_HOOKS_OFF = ("-c", "core.hooksPath=/dev/null")
_BOT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Slack Bot Agent",
    "GIT_AUTHOR_EMAIL": "bot@localhost",
    "GIT_COMMITTER_NAME": "Slack Bot Agent",
    "GIT_COMMITTER_EMAIL": "bot@localhost",
}
_SUBJECT_PREFIX = "bot: "
_SUBJECT_MAX = 72


def _run_subprocess(argv: list[str]) -> str:
    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env={**os.environ, **_BOT_IDENTITY, "GIT_TERMINAL_PROMPT": "0"},
    )
    if completed.returncode != 0:
        raise RuntimeError("git 명령이 실패했습니다.")
    return completed.stdout


class GitCommands:
    def __init__(self, runner: GitRunner = _run_subprocess) -> None:
        self._runner = runner

    def __call__(self, repo: Path, *args: str) -> str:
        _check_allowed(args)
        return self._runner(["git", "-C", str(repo), *_HOOKS_OFF, *args])


def _check_allowed(args: Sequence[str]) -> None:
    subcommand = args[0] if args else ""
    if subcommand not in _ALLOWED_SUBCOMMANDS:
        raise ValueError(f"허용되지 않은 git 명령입니다: {subcommand}")
    if subcommand == "worktree" and (len(args) < 2 or args[1] not in _ALLOWED_WORKTREE_ACTIONS):
        raise ValueError("허용되지 않은 git worktree 동작입니다")
    if subcommand == "branch" and not (
        len(args) == 3 and args[1] == "-D" and args[2].startswith(BOT_BRANCH_PREFIX)
    ):
        raise ValueError("봇이 만든 브랜치만 삭제할 수 있습니다")


class WorkspaceError(Exception):
    """A worktree operation failed; the message is safe to show in Slack."""


class UncommittedChanges(WorkspaceError):
    def __init__(self, files: list[str]) -> None:
        super().__init__(
            "원본 체크아웃에 커밋하지 않은 변경이 있는 파일이 있습니다: "
            + ", ".join(f"`{path}`" for path in files)
            + ". 먼저 커밋하거나 정리한 뒤 다시 요청해 주세요."
        )
        self.files = files


def _safe(value: str) -> str:
    """Letters, digits and single dashes only, never starting with a dash."""
    return re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-")


def thread_key(channel_id: str, thread_ts: str) -> str:
    key = _safe(f"{channel_id}-{thread_ts}")
    if not key:
        raise WorkspaceError("스레드 정보를 확인할 수 없습니다.")
    return key


class ThreadWorkspaces:
    """One git worktree and `bot/<thread>` branch per Slack thread, outside the project."""

    def __init__(self, worktrees_root: Path, git: GitCommands | None = None) -> None:
        self._root = worktrees_root
        self._git = git or GitCommands()

    def branch_name(self, channel_id: str, thread_ts: str) -> str:
        return BOT_BRANCH_PREFIX + thread_key(channel_id, thread_ts)

    def worktree_path(self, project_name: str, channel_id: str, thread_ts: str) -> Path:
        return self._root / (_safe(project_name) or "project") / thread_key(channel_id, thread_ts)

    def existing(self, project_name: str, channel_id: str, thread_ts: str) -> Path | None:
        path = self.worktree_path(project_name, channel_id, thread_ts)
        return path if path.is_dir() else None

    def ensure(
        self, project_root: Path, project_name: str, channel_id: str, thread_ts: str
    ) -> Path | None:
        """The thread's worktree, created from the project's HEAD on first use.
        `None` when the project is not the top level of a git repository."""
        if not self.can_isolate(project_root):
            return None
        path = self.worktree_path(project_name, channel_id, thread_ts)
        if path.is_dir():
            return path
        branch = self.branch_name(channel_id, thread_ts)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._git(project_root, "worktree", "add", "-b", branch, str(path), "HEAD")
        except (OSError, RuntimeError):
            raise WorkspaceError("작업용 worktree를 만들지 못했습니다.") from None
        return path

    def check_clean(self, project_root: Path, paths: list[str]) -> None:
        try:
            status = self._git(project_root, "status", "--porcelain", "--", *paths)
        except RuntimeError:
            raise WorkspaceError("원본 체크아웃의 상태를 확인하지 못했습니다.") from None
        dirty = [line[3:] for line in status.splitlines() if line.strip()]
        if dirty:
            raise UncommittedChanges(dirty)

    def commit(self, workspace: Path, paths: list[str], goal: str) -> bool:
        """Commit only `paths`. `False` when they hold no change."""
        try:
            if not self._git(workspace, "status", "--porcelain", "--", *paths).strip():
                return False
            self._git(workspace, "add", "--", *paths)
            self._git(workspace, "commit", "--no-verify", "-m", _commit_subject(goal), "--", *paths)
        except RuntimeError:
            raise WorkspaceError("worktree 변경을 커밋하지 못했습니다.") from None
        return True

    def discard(
        self, project_root: Path, project_name: str, channel_id: str, thread_ts: str
    ) -> bool:
        path = self.existing(project_name, channel_id, thread_ts)
        if path is None:
            return False
        self._remove(project_root, path, self.branch_name(channel_id, thread_ts))
        return True

    def discard_thread(self, channel_id: str, thread_ts: str) -> bool:
        """Discard this thread's worktree whichever project it belongs to. The original
        repository is found from the worktree itself, so this survives a bot restart."""
        key = thread_key(channel_id, thread_ts)
        if not self._root.is_dir():
            return False
        for project_dir in sorted(self._root.iterdir()):
            path = project_dir / key
            if not path.is_dir():
                continue
            try:
                common = self._git(path, "rev-parse", "--git-common-dir").strip()
            except RuntimeError:
                raise WorkspaceError("worktree의 원본 저장소를 찾지 못했습니다.") from None
            self._remove(
                (path / common).resolve().parent, path, self.branch_name(channel_id, thread_ts)
            )
            return True
        return False

    def _remove(self, project_root: Path, path: Path, branch: str) -> None:
        try:
            self._git(project_root, "worktree", "remove", "--force", str(path))
            self._git(project_root, "branch", "-D", branch)
            self._git(project_root, "worktree", "prune")
        except RuntimeError:
            raise WorkspaceError("worktree를 폐기하지 못했습니다.") from None

    def can_isolate(self, project_root: Path) -> bool:
        """Only a project that is the top level of its own git repository can be isolated."""
        return self._is_repository_root(project_root)

    def _is_repository_root(self, project_root: Path) -> bool:
        try:
            top = self._git(project_root, "rev-parse", "--show-toplevel").strip()
        except RuntimeError:
            return False
        return Path(top).resolve() == project_root.resolve()


def _commit_subject(goal: str) -> str:
    first_line = " ".join(goal.splitlines()[0].split()) if goal.strip() else "코드 작업"
    return (_SUBJECT_PREFIX + first_line)[:_SUBJECT_MAX]

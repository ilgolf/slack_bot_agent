"""Thread worktree management (plan.md Phase 17). Git is a fake in section A."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.thread_workspace import GitCommands


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(argv)
        return ""


@pytest.mark.parametrize(
    "args",
    [
        ("push", "origin", "main"),
        ("merge", "main"),
        ("reset", "--hard"),
        ("checkout", "main"),
        ("config", "user.name", "x"),
        ("rebase", "main"),
        ("-c", "core.hooksPath=/tmp/evil", "status"),
        ("worktree", "move", "a", "b"),
        ("branch", "--list"),
        ("show", "--stat"),
        ("show", "HEAD"),
        ("show", "main:a.txt"),
        ("show", "HEAD:a.txt", "extra"),
    ],
)
def test_git_commands_outside_the_allowlist_are_rejected_without_running(
    args: tuple[str, ...],
) -> None:
    runner = RecordingRunner()

    with pytest.raises(ValueError):
        GitCommands(runner)(Path("/repo"), *args)

    assert runner.calls == []


def test_every_git_call_disables_hooks_and_runs_in_the_given_repository() -> None:
    runner = RecordingRunner()
    git = GitCommands(runner)

    git(Path("/repo"), "status", "--porcelain")
    git(Path("/repo"), "worktree", "add", "/wt", "-b", "bot/t")
    git(Path("/repo"), "commit", "--no-verify", "-m", "msg")

    assert len(runner.calls) == 3
    for argv in runner.calls:
        assert argv[:6] == ["git", "-C", "/repo", "-c", "core.hooksPath=/dev/null", argv[5]]
        assert argv[5] in {"status", "worktree", "commit"}


def test_default_runner_uses_an_argument_list_without_a_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen["argv"], seen["kwargs"] = argv, kwargs
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    output = GitCommands()(Path("/repo"), "rev-parse", "HEAD")

    assert output == "ok\n"
    assert isinstance(seen["argv"], list)
    kwargs = seen["kwargs"]
    assert isinstance(kwargs, dict)
    assert not kwargs.get("shell")
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_show_is_limited_to_reading_one_file_at_head() -> None:
    runner = RecordingRunner()

    GitCommands(runner)(Path("/repo"), "show", "HEAD:src/a.py")

    assert runner.calls[0][-2:] == ["show", "HEAD:src/a.py"]


def test_branch_delete_is_limited_to_bot_branches() -> None:
    runner = RecordingRunner()
    git = GitCommands(runner)

    git(Path("/repo"), "branch", "-D", "bot/C1-1-1")
    with pytest.raises(ValueError):
        git(Path("/repo"), "branch", "-D", "main")
    with pytest.raises(ValueError):
        git(Path("/repo"), "branch", "-d", "bot/C1-1-1")

    assert len(runner.calls) == 1


# --- B. worktree lifecycle against real temporary git repositories -------------------

from src.thread_workspace import ThreadWorkspaces, UncommittedChanges, WorkspaceError  # noqa: E402

_TEST_IDENTITY = ("-c", "user.name=Tester", "-c", "user.email=t@example.com")


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *_TEST_IDENTITY, *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "projects" / "demo"
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    (root / "a.txt").write_text("one\n")
    (root / "b.txt").write_text("two\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture
def workspaces(tmp_path: Path) -> ThreadWorkspaces:
    return ThreadWorkspaces(tmp_path / "worktrees")


def test_a_project_that_is_not_a_git_repository_gets_no_worktree(
    tmp_path: Path, workspaces: ThreadWorkspaces
) -> None:
    plain = tmp_path / "projects" / "plain"
    plain.mkdir(parents=True)

    assert workspaces.ensure(plain, "plain", "C1", "1.1") is None
    assert not (tmp_path / "worktrees").exists()


def test_ensure_creates_a_bot_branch_worktree_at_the_original_head(
    repo: Path, workspaces: ThreadWorkspaces, tmp_path: Path
) -> None:
    path = workspaces.ensure(repo, "demo", "C1", "1700000000.000100")

    assert path == tmp_path / "worktrees" / "demo" / "C1-1700000000-000100"
    assert _git(path, "rev-parse", "HEAD") == _git(repo, "rev-parse", "HEAD")
    assert _git(path, "rev-parse", "--abbrev-ref", "HEAD") == "bot/C1-1700000000-000100"
    assert (path / "a.txt").read_text() == "one\n"


def test_same_thread_reuses_its_worktree_and_other_threads_get_their_own(
    repo: Path, workspaces: ThreadWorkspaces
) -> None:
    first = workspaces.ensure(repo, "demo", "C1", "1.1")
    (first / "a.txt").write_text("edited\n")  # type: ignore[operator]

    again = workspaces.ensure(repo, "demo", "C1", "1.1")
    other = workspaces.ensure(repo, "demo", "C1", "2.2")

    assert again == first
    assert (again / "a.txt").read_text() == "edited\n"  # type: ignore[operator]
    assert other is not None and other != first
    assert (other / "a.txt").read_text() == "one\n"


@pytest.mark.parametrize("channel", ["../../etc", "-rf", "C1/ x;$(y)", "..", ""])
def test_hostile_thread_values_cannot_escape_the_root_or_inject_options(
    repo: Path, workspaces: ThreadWorkspaces, tmp_path: Path, channel: str
) -> None:
    root = (tmp_path / "worktrees").resolve()

    try:
        path = workspaces.ensure(repo, "../demo", channel, "-1.1")
    except WorkspaceError:
        return

    assert path is not None
    assert path.resolve().is_relative_to(root)
    branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
    assert branch.startswith("bot/") and not branch.removeprefix("bot/").startswith("-")


def test_a_post_checkout_hook_in_the_project_does_not_run(
    repo: Path, workspaces: ThreadWorkspaces, tmp_path: Path
) -> None:
    marker = tmp_path / "hook-ran"
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)

    assert workspaces.ensure(repo, "demo", "C1", "1.1") is not None
    assert not marker.exists()


def test_uncommitted_changes_in_approved_files_are_rejected(
    repo: Path, workspaces: ThreadWorkspaces
) -> None:
    workspaces.check_clean(repo, ["a.txt", "new.txt"])  # clean tree and a not-yet-existing file

    (repo / "a.txt").write_text("dirty\n")
    (repo / "new.txt").write_text("untracked\n")

    with pytest.raises(UncommittedChanges) as raised:
        workspaces.check_clean(repo, ["a.txt", "new.txt", "b.txt"])
    assert raised.value.files == ["a.txt", "new.txt"]
    workspaces.check_clean(repo, ["b.txt"])  # unrelated dirty files do not matter


def test_commit_records_only_the_given_files_with_a_fixed_author_and_short_subject(
    repo: Path, workspaces: ThreadWorkspaces, tmp_path: Path
) -> None:
    path = workspaces.ensure(repo, "demo", "C1", "1.1")
    assert path is not None
    marker = tmp_path / "pre-commit-ran"
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    hook.chmod(0o755)
    (path / "a.txt").write_text("changed\n")
    (path / "b.txt").write_text("not approved\n")
    goal = "첫 줄 목표 " + "아주 긴 설명 " * 30 + "\n두 번째 줄"

    committed = workspaces.commit(path, ["a.txt"], goal)

    assert committed is True
    assert not marker.exists()
    assert _git(path, "show", "--name-only", "--pretty=format:", "HEAD") == "a.txt"
    assert _git(path, "status", "--porcelain") == "M b.txt"
    assert _git(path, "log", "-1", "--pretty=%an <%ae>") == "Slack Bot Agent <bot@localhost>"
    subject = _git(path, "log", "-1", "--pretty=%s")
    assert len(subject) <= 72 and "\n" not in subject and subject.startswith("bot: 첫 줄 목표")


def test_commit_with_nothing_to_record_returns_false(
    repo: Path, workspaces: ThreadWorkspaces
) -> None:
    path = workspaces.ensure(repo, "demo", "C1", "1.1")
    assert path is not None

    assert workspaces.commit(path, ["a.txt"], "아무 변경 없음") is False


def test_original_checkout_is_untouched_by_worktree_work(
    repo: Path, workspaces: ThreadWorkspaces
) -> None:
    head, branch = _git(repo, "rev-parse", "HEAD"), _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    path = workspaces.ensure(repo, "demo", "C1", "1.1")
    assert path is not None
    (path / "a.txt").write_text("changed\n")

    workspaces.commit(path, ["a.txt"], "변경")

    assert _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == branch == "main"
    assert (repo / "a.txt").read_text() == "one\n"
    assert _git(repo, "status", "--porcelain") == ""


def test_discard_removes_the_worktree_and_branch_but_not_the_original(
    repo: Path, workspaces: ThreadWorkspaces
) -> None:
    path = workspaces.ensure(repo, "demo", "C1", "1.1")
    assert path is not None
    (path / "a.txt").write_text("changed\n")
    workspaces.commit(path, ["a.txt"], "변경")

    assert workspaces.discard(repo, "demo", "C1", "1.1") is True

    assert not path.exists()
    assert _git(repo, "branch", "--list", "bot/*") == ""
    assert str(path) not in _git(repo, "worktree", "list")
    assert (repo / "a.txt").read_text() == "one\n"
    assert workspaces.discard(repo, "demo", "C1", "1.1") is False


def test_thread_worktree_finds_a_threads_worktree_without_knowing_its_project(
    repo: Path, workspaces: ThreadWorkspaces
) -> None:
    created = workspaces.ensure(repo, "demo", "C1", "1.1")

    assert workspaces.thread_worktree("C1", "1.1") == created
    assert workspaces.thread_worktree("C1", "9.9") is None
    assert ThreadWorkspaces(repo.parent / "missing").thread_worktree("C1", "1.1") is None

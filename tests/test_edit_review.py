"""Post-run review of a worktree after the agent edited it (plan.md Phase 18, C).

Runs against real temporary git repositories: the review reads `git status`, never the
agent's own account of what it did.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.edit_review import EditReview, review_worktree
from src.thread_workspace import GitCommands


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@t", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    root = tmp_path / "wt"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "a.txt").write_text("one\n")
    (root / "b.txt").write_text("two\n")
    (root / "big.txt").write_text("".join(f"line {n}\n" for n in range(30)))
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _review(worktree: Path, named: frozenset[str] = frozenset()) -> EditReview:
    return review_worktree(GitCommands(), worktree, named_paths=named)


def test_changed_files_and_diffs_come_from_git_status_for_new_and_modified_files(
    worktree: Path,
) -> None:
    (worktree / "a.txt").write_text("one\nadded\n")
    (worktree / "new").mkdir()
    (worktree / "new" / "c.txt").write_text("fresh\n")

    review = _review(worktree)

    assert review.changed_files == ["a.txt", "new/c.txt"]
    assert review.violations == []
    assert "+added" in review.diffs[0] and "a/a.txt" in review.diffs[0]
    assert "+fresh" in review.diffs[1] and "b/new/c.txt" in review.diffs[1]


def test_a_clean_worktree_has_no_changes(worktree: Path) -> None:
    review = _review(worktree)

    assert (review.changed_files, review.diffs, review.violations) == ([], [], [])


def test_deleted_and_renamed_files_are_violations(worktree: Path) -> None:
    (worktree / "a.txt").unlink()
    _git(worktree, "mv", "b.txt", "moved.txt")

    review = _review(worktree)

    joined = "\n".join(review.violations)
    assert "`a.txt`" in joined and "삭제" in joined
    assert "`moved.txt`" in joined or "`b.txt`" in joined
    assert "이름" in joined


@pytest.mark.parametrize("path", ["plan.md", "CLAUDE.md", "tests/conftest.py", "scripts/run.sh"])
def test_unnamed_management_or_code_executing_files_are_violations(
    worktree: Path, path: str
) -> None:
    target = worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x\n")

    assert any(f"`{path}`" in violation for violation in _review(worktree).violations)
    assert _review(worktree, frozenset({path})).violations == []


def test_env_files_are_violations_even_when_named(worktree: Path) -> None:
    (worktree / ".env.local").write_text("K=1\n")

    assert any("`.env.local`" in v for v in _review(worktree, frozenset({".env.local"})).violations)


def test_blanking_a_file_and_dropping_most_of_a_big_one_are_violations(worktree: Path) -> None:
    (worktree / "a.txt").write_text("")
    (worktree / "big.txt").write_text("line 0\nline 1\nnew\n")

    review = _review(worktree)

    assert any("`a.txt`" in v and "비우" in v for v in review.violations)
    assert any("`big.txt`" in v and "대부분" in v for v in review.violations)
    assert _review(worktree, frozenset({"big.txt"})).violations == [
        v for v in review.violations if "`a.txt`" in v
    ]


def test_more_than_twenty_files_is_a_violation(worktree: Path) -> None:
    for n in range(21):
        (worktree / f"gen{n}.txt").write_text("x\n")

    assert any("20개" in v for v in _review(worktree).violations)


def test_more_than_two_megabytes_in_total_is_a_violation(worktree: Path) -> None:
    for name in ("p.txt", "q.txt", "r.txt"):
        (worktree / name).write_text("x" * 800_000)

    assert any("크기" in v for v in _review(worktree).violations)


def test_non_text_changes_are_violations(worktree: Path) -> None:
    (worktree / "blob.bin").write_bytes(b"\xff\xfe\x00\x01")

    assert any("`blob.bin`" in v for v in _review(worktree).violations)


def test_violation_messages_name_paths_but_never_file_content(worktree: Path) -> None:
    (worktree / ".env").write_text("TOPSECRETVALUE=1\n")
    (worktree / "plan.md").write_text("PRIVATEBODY\n")
    (worktree / "a.txt").write_text("")

    text = "\n".join(_review(worktree).violations)

    assert "TOPSECRETVALUE" not in text and "PRIVATEBODY" not in text and "one" not in text


def _plan_first_review(worktree: Path, named: frozenset[str] = frozenset()) -> EditReview:
    return review_worktree(
        GitCommands(), worktree, named_paths=named, write_roots=("docs/",), plan_first=True
    )


def test_changes_outside_the_write_roots_are_violations(worktree: Path) -> None:
    (worktree / "docs").mkdir()
    (worktree / "docs" / "design.md").write_text("기획\n")
    (worktree / "a.txt").write_text("one\nchanged\n")

    review = _plan_first_review(worktree)

    assert [v.split(":")[0] for v in review.violations] == ["`a.txt`"]
    assert review.changed_files == ["a.txt", "docs/design.md"]


def test_a_plan_the_agent_marked_confirmed_is_a_violation(worktree: Path) -> None:
    plan = worktree / "docs" / "linear" / "plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# 기획\n\n상태: 확정\n")

    review = _plan_first_review(worktree, frozenset({"docs/linear/plan.md"}))

    assert len(review.violations) == 1
    assert "docs/linear/plan.md" in review.violations[0] and "확정" in review.violations[0]


def test_flipping_an_existing_draft_to_confirmed_is_a_violation(worktree: Path) -> None:
    plan = worktree / "docs" / "linear" / "plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# 기획\n\n상태: 초안\n\n본문\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-q", "-m", "draft")
    plan.write_text("# 기획\n\n상태: 확정\n\n본문\n")

    review = _plan_first_review(worktree, frozenset({"docs/linear/plan.md"}))

    assert len(review.violations) == 1 and "확정" in review.violations[0]


def test_editing_a_draft_plan_or_an_already_confirmed_one_keeps_the_status_legal(
    worktree: Path,
) -> None:
    plan = worktree / "docs" / "linear" / "plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# 기획\n\n상태: 초안\n\n본문\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-q", "-m", "draft")
    plan.write_text("# 기획\n\n상태: 초안\n\n본문 보강\n")
    assert _plan_first_review(worktree, frozenset({"docs/linear/plan.md"})).violations == []

    plan.write_text("# 기획\n\n상태: 확정\n\n본문\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-q", "-m", "confirmed")
    plan.write_text("# 기획\n\n상태: 확정\n\n본문 보강\n")
    assert _plan_first_review(worktree, frozenset({"docs/linear/plan.md"})).violations == []


def test_without_plan_first_nothing_about_plans_or_roots_is_checked(worktree: Path) -> None:
    plan = worktree / "docs" / "linear" / "plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# 기획\n\n상태: 확정\n")
    (worktree / "a.txt").write_text("one\nchanged\n")

    assert _review(worktree, frozenset({"docs/linear/plan.md"})).violations == []

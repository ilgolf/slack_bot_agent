"""Write/Edit calls are judged before they run (plan.md Phase 18, A)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.edit_guard import is_allowed_edit, is_allowed_tool_call


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    root = tmp_path / "wt"
    root.mkdir()
    (root / "a.py").write_text("inside")
    (tmp_path / "secret.txt").write_text("outside")
    return root


def _call(tool: str, path: str) -> dict[str, str]:
    if tool == "Write":
        return {"file_path": path, "content": "x"}
    return {"file_path": path, "old_string": "a", "new_string": "b"}


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_edit_inside_the_worktree_is_allowed(worktree: Path, tool: str) -> None:
    assert is_allowed_edit(worktree, tool, _call(tool, str(worktree / "a.py")))
    assert is_allowed_edit(worktree, tool, _call(tool, str(worktree / "new" / "b.py")))


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_edit_outside_the_worktree_is_denied(worktree: Path, tmp_path: Path, tool: str) -> None:
    assert not is_allowed_edit(worktree, tool, _call(tool, str(tmp_path / "secret.txt")))
    assert not is_allowed_edit(worktree, tool, _call(tool, str(worktree / ".." / "secret.txt")))


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_edit_through_a_symlink_pointing_outside_is_denied(
    worktree: Path, tmp_path: Path, tool: str
) -> None:
    (worktree / "link.txt").symlink_to(tmp_path / "secret.txt")

    assert not is_allowed_edit(worktree, tool, _call(tool, str(worktree / "link.txt")))


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_relative_edit_path_is_judged_against_the_worktree(worktree: Path, tool: str) -> None:
    assert is_allowed_edit(worktree, tool, _call(tool, "a.py"))
    assert not is_allowed_edit(worktree, tool, _call(tool, "../secret.txt"))


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize(
    "relative",
    [".git", ".git/config", "sub/.git/hooks/pre-commit", ".GIT/config", "./.git", "a/../.git"],
)
def test_git_metadata_is_never_editable(worktree: Path, tool: str, relative: str) -> None:
    assert not is_allowed_edit(worktree, tool, _call(tool, relative))
    assert not is_allowed_edit(worktree, tool, _call(tool, str(worktree / relative)))


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_git_look_alike_names_stay_editable(worktree: Path, tool: str) -> None:
    for name in (".gitignore", ".gitattributes", "docs/git.md", "gitlab.py"):
        assert is_allowed_edit(worktree, tool, _call(tool, name))


GUARDED = [
    "plan.md",
    "CLAUDE.md",
    "AGENTS.md",
    ".claude/settings.json",
    "tests/conftest.py",
    "pyproject.toml",
    ".github/workflows/ci.yml",
    "scripts/run.sh",
]


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize("relative", GUARDED)
def test_unnamed_management_and_code_executing_files_are_denied(
    worktree: Path, tool: str, relative: str
) -> None:
    assert not is_allowed_edit(worktree, tool, _call(tool, relative))
    assert not is_allowed_edit(worktree, tool, _call(tool, str(worktree / relative)))


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize("relative", GUARDED)
def test_the_same_files_are_allowed_when_the_user_named_them(
    worktree: Path, tool: str, relative: str
) -> None:
    assert is_allowed_edit(worktree, tool, _call(tool, relative), named_paths={relative})
    assert is_allowed_edit(
        worktree, tool, _call(tool, str(worktree / relative)), named_paths={relative}
    )


def test_naming_one_file_does_not_unlock_another(worktree: Path) -> None:
    assert not is_allowed_edit(
        worktree, "Write", _call("Write", "pyproject.toml"), named_paths={"plan.md"}
    )
    assert not is_allowed_edit(
        worktree, "Write", _call("Write", "other/plan.md"), named_paths={"plan.md"}
    )


SECRETS = [".env", ".env.local", "config/.env.production", "./.env", ".ENV", "sub\\.env", "a//.env"]


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize("relative", SECRETS)
def test_env_files_are_denied_even_when_the_user_named_them(
    worktree: Path, tool: str, relative: str
) -> None:
    assert not is_allowed_edit(worktree, tool, _call(tool, relative))
    assert not is_allowed_edit(worktree, tool, _call(tool, relative), named_paths={relative})
    assert not is_allowed_edit(
        worktree, tool, _call(tool, str(worktree / relative)), named_paths={relative}
    )


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_env_template_files_stay_editable(worktree: Path, tool: str) -> None:
    for name in (".env.example", "config/.env.sample", "docs/env.md"):
        assert is_allowed_edit(worktree, tool, _call(tool, name))


def test_write_larger_than_one_megabyte_is_denied(worktree: Path) -> None:
    just_fits = {"file_path": "big.txt", "content": "x" * 1_000_000}
    too_big = {"file_path": "big.txt", "content": "x" * 1_000_001}

    assert is_allowed_edit(worktree, "Write", just_fits)
    assert not is_allowed_edit(worktree, "Write", too_big)


def test_write_size_counts_bytes_not_characters(worktree: Path) -> None:
    korean = {"file_path": "k.txt", "content": "가" * 400_000}  # 1.2MB in UTF-8

    assert not is_allowed_edit(worktree, "Write", korean)


@pytest.mark.parametrize("blank", ["", "  \n\t\n"])
def test_write_that_blanks_a_non_empty_file_is_denied(worktree: Path, blank: str) -> None:
    assert not is_allowed_edit(worktree, "Write", {"file_path": "a.py", "content": blank})


def test_blank_write_is_fine_for_empty_or_missing_files_and_real_content_is_fine(
    worktree: Path,
) -> None:
    (worktree / "empty.txt").write_text("")

    assert is_allowed_edit(worktree, "Write", {"file_path": "empty.txt", "content": ""})
    assert is_allowed_edit(worktree, "Write", {"file_path": "new.txt", "content": ""})
    assert is_allowed_edit(worktree, "Write", {"file_path": "a.py", "content": "replaced\n"})


def test_non_string_write_content_is_denied(worktree: Path) -> None:
    assert not is_allowed_edit(worktree, "Write", {"file_path": "a.py", "content": 123})
    assert not is_allowed_edit(worktree, "Write", {"file_path": "a.py"})


def test_tool_call_dispatch_keeps_reads_inside_the_worktree(worktree: Path, tmp_path: Path) -> None:
    inside = str(worktree / "a.py")
    outside = str(tmp_path / "secret.txt")

    assert is_allowed_tool_call(worktree, "Read", {"file_path": inside})
    assert not is_allowed_tool_call(worktree, "Read", {"file_path": outside})
    assert is_allowed_tool_call(worktree, "Grep", {"pattern": "x"})
    assert not is_allowed_tool_call(worktree, "Grep", {"pattern": "x", "path": str(tmp_path)})
    assert is_allowed_tool_call(worktree, "Glob", {"pattern": "src/**/*.py"})
    assert not is_allowed_tool_call(worktree, "Glob", {"pattern": "/etc/*"})


def test_tool_call_dispatch_applies_the_edit_rules_to_write_and_edit(worktree: Path) -> None:
    assert is_allowed_tool_call(worktree, "Write", {"file_path": "n.py", "content": "x"})
    assert not is_allowed_tool_call(worktree, "Write", {"file_path": ".env", "content": "x"})
    assert not is_allowed_tool_call(worktree, "Edit", {"file_path": "plan.md"})
    assert is_allowed_tool_call(
        worktree,
        "Edit",
        {"file_path": "plan.md", "old_string": "a", "new_string": "b"},
        named_paths={"plan.md"},
    )


@pytest.mark.parametrize(
    "tool", ["Bash", "NotebookEdit", "WebFetch", "WebSearch", "Task", "TodoWrite", "mcp__x__y", ""]
)
def test_every_other_tool_is_denied(worktree: Path, tool: str) -> None:
    assert not is_allowed_tool_call(worktree, tool, {"command": "echo hi"})
    assert not is_allowed_tool_call(worktree, tool, {"file_path": "a.py"})


@pytest.mark.parametrize("tool", ["Read", "Grep", "Glob", "Write", "Edit"])
@pytest.mark.parametrize(
    "tool_input",
    [
        None,
        [],
        "a.py",
        {},
        {"file_path": None},
        {"file_path": 123},
        {"file_path": ["a.py"]},
        {"file_path": ""},
        {"file_path": "."},
        {"file_path": "bad\x00name.py"},
        {"path": 123, "pattern": 1},
        {"path": "\x00", "pattern": "*.py"},
    ],
)
def test_malformed_or_failing_judgements_are_denied_not_raised(
    worktree: Path, tool: str, tool_input: object
) -> None:
    assert is_allowed_tool_call(worktree, tool, tool_input) is False  # type: ignore[arg-type]

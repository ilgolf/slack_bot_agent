"""`gh pr create` 어댑터 (plan.md Phase 33 C). 실행기는 주입한 fake다."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.code.github import GhCli, GhResult, PullRequestError, run_gh
from src.code.pull_request import PullRequestDraft


class FakeGh:
    def __init__(self, result: GhResult | Exception) -> None:
        self.result = result
        self.calls: list[tuple[list[str], Path]] = []

    def __call__(self, argv: list[str], cwd: Path) -> GhResult:
        self.calls.append((argv, cwd))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_creating_a_pull_request_runs_one_fixed_argv_and_returns_the_url() -> None:
    gh = FakeGh(GhResult(0, "https://github.com/acme/repo/pull/7\n", ""))

    url = GhCli(gh).create_pull_request(
        Path("/repo"),
        head="bot/C1-1.0",
        base="main",
        draft=PullRequestDraft(title="--title 제목", body="변경한 파일:\n- `a.py`"),
    )

    assert url == "https://github.com/acme/repo/pull/7"
    assert gh.calls == [
        (
            [
                "gh",
                "pr",
                "create",
                "--head",
                "bot/C1-1.0",
                "--base",
                "main",
                "--title",
                "--title 제목",
                "--body",
                "변경한 파일:\n- `a.py`",
            ],
            Path("/repo"),
        )
    ]


def _create(gh: FakeGh) -> str:
    return GhCli(gh).create_pull_request(
        Path("/repo"), head="bot/a", base="main", draft=PullRequestDraft(title="t", body="b")
    )


@pytest.mark.parametrize(
    ("result", "code"),
    [
        (GhResult(4, "", "To get started with GitHub CLI, please run:  gh auth login"), "auth"),
        (GhResult(1, "", "gh: authentication failed (HTTP 401)"), "auth"),
        (
            GhResult(
                1,
                "",
                'a pull request for branch "bot/a" into branch "main" already exists:\n'
                "https://github.com/acme/repo/pull/3",
            ),
            "exists",
        ),
        (TimeoutError(), "timeout"),
        (GhResult(1, "", "something else broke"), "failed"),
        (GhResult(0, "", ""), "failed"),
    ],
)
def test_failures_are_told_apart_and_never_retried(result: GhResult | Exception, code: str) -> None:
    gh = FakeGh(result)

    with pytest.raises(PullRequestError) as raised:
        _create(gh)

    assert raised.value.code == code
    assert len(gh.calls) == 1


def test_an_existing_pull_request_error_carries_its_url() -> None:
    gh = FakeGh(GhResult(1, "", "already exists:\nhttps://github.com/acme/repo/pull/3\n"))

    with pytest.raises(PullRequestError) as raised:
        _create(gh)

    assert raised.value.url == "https://github.com/acme/repo/pull/3"


def test_the_default_branch_is_read_with_one_fixed_argv() -> None:
    gh = FakeGh(GhResult(0, "main\n", ""))

    branch = GhCli(gh).default_branch(Path("/repo"))

    assert branch == "main"
    assert gh.calls == [
        (
            ["gh", "repo", "view", "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name"],
            Path("/repo"),
        )
    ]


@pytest.mark.parametrize("output", ["", "--delete", "ma in", "main\nother", "a:b", "-x"])
def test_an_unusable_default_branch_name_is_a_failure_not_an_argument(output: str) -> None:
    with pytest.raises(PullRequestError) as raised:
        GhCli(FakeGh(GhResult(0, output, ""))).default_branch(Path("/repo"))

    assert raised.value.code == "failed"


def test_the_real_runner_uses_no_shell_a_timeout_and_the_repo_as_cwd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> object:
        seen["argv"] = argv
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="out", stderr="err")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_gh(["gh", "pr", "create"], Path("/repo"))

    assert result == GhResult(0, "out", "err")
    assert seen["argv"] == ["gh", "pr", "create"]
    assert seen["cwd"] == Path("/repo")
    assert seen.get("shell", False) is False
    assert seen["timeout"] == 60
    assert seen["stdin"] == subprocess.DEVNULL


def test_the_real_runner_turns_a_subprocess_timeout_into_a_timeout_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(argv: list[str], **kwargs: object) -> object:
        raise subprocess.TimeoutExpired(argv, 60)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(TimeoutError):
        run_gh(["gh", "pr", "create"], Path("/repo"))


def test_a_missing_gh_binary_is_a_plain_failure() -> None:
    with pytest.raises(PullRequestError) as raised:
        _create(FakeGh(FileNotFoundError("gh")))

    assert raised.value.code == "failed"

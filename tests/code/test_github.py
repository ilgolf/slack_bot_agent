"""`gh pr create` 어댑터 (plan.md Phase 33 C). 실행기는 주입한 fake다."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.code.github import GhCli, GhResult, PullRequestError
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

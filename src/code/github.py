"""`gh pr create`를 고정 argv로 실행한다 (plan.md Phase 33, docs/code/plan.md 슬라이스 3).

셸을 거치지 않고, 제목·본문 같은 외부 문자열은 argv 항목 하나로만 넘긴다.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from src.code.pull_request import PullRequestDraft
from src.code.workspace import is_safe_ref


@dataclass(frozen=True)
class GhResult:
    returncode: int
    stdout: str
    stderr: str


GhRunner = Callable[[list[str], Path], GhResult]

_URL = re.compile(r"https://\S+")
_AUTH_MARKERS = ("gh auth login", "authentication", "not logged in", "bad credentials")
_MESSAGES = {
    "auth": "gh 인증이 되어 있지 않거나 만료되었습니다. 이 PC에서 `gh auth login`을 확인해 주세요.",
    "exists": "이 브랜치의 PR이 이미 있습니다.",
    "timeout": "gh가 제한 시간 안에 끝나지 않았습니다. PR이 만들어졌는지 GitHub에서 확인해 주세요.",
    "failed": "PR을 만들지 못했습니다.",
}


class PullRequestError(Exception):
    """`code`: auth | exists | timeout | failed. `url`은 이미 있는 PR의 주소다."""

    def __init__(self, code: str, url: str | None = None) -> None:
        super().__init__(_MESSAGES[code])
        self.code = code
        self.url = url


GH_TIMEOUT_SECONDS = 60


def run_gh(argv: list[str], cwd: Path) -> GhResult:
    """셸 없이 `gh`를 실행한다. 입력은 막고(`stdin`) 시간 제한을 둔다."""
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError from None
    return GhResult(completed.returncode, completed.stdout, completed.stderr)


class GhCli:
    def __init__(self, runner: GhRunner) -> None:
        self._runner = runner

    def create_pull_request(
        self, repo: Path, *, head: str, base: str, draft: PullRequestDraft
    ) -> str:
        argv = [
            "gh",
            "pr",
            "create",
            "--head",
            head,
            "--base",
            base,
            "--title",
            draft.title,
            "--body",
            draft.body,
        ]
        url = self._run(argv, repo)
        if not url:
            raise PullRequestError("failed")
        return url

    def default_branch(self, repo: Path) -> str:
        """저장소의 기본 브랜치 이름. 다른 명령의 인자로 쓰이므로 안전한 이름만 돌려준다."""
        name = self._run(
            ["gh", "repo", "view", "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name"],
            repo,
        )
        if not is_safe_ref(name):
            raise PullRequestError("failed")
        return name

    def _run(self, argv: list[str], repo: Path) -> str:
        try:
            result = self._runner(argv, repo)
        except TimeoutError:
            raise PullRequestError("timeout") from None
        except OSError:
            raise PullRequestError("failed") from None
        if result.returncode != 0:
            raise _failure(result.stderr)
        return result.stdout.strip()


def _failure(stderr: str) -> PullRequestError:
    text = stderr.casefold()
    if "already exists" in text:
        found = _URL.search(stderr)
        return PullRequestError("exists", found.group(0) if found else None)
    if any(marker in text for marker in _AUTH_MARKERS):
        return PullRequestError("auth")
    return PullRequestError("failed")

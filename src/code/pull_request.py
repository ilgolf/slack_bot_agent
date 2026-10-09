"""PR 제목·본문을 코드가 만든다 (plan.md Phase 33, docs/code/plan.md Q3). LLM은 쓰지 않는다."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

_TITLE = re.compile(r"PR\s*제목\s*[:：]\s*(\S[^\n]*)")


@dataclass(frozen=True)
class PullRequestDraft:
    title: str
    body: str


def build_pull_request(
    command: str, *, commit_subject: str, changed_files: Sequence[str]
) -> PullRequestDraft:
    match = _TITLE.search(command)
    return PullRequestDraft(
        title=match.group(1).strip() if match else commit_subject, body=_body(changed_files)
    )


def _body(changed_files: Sequence[str]) -> str:
    return "변경한 파일:\n" + "\n".join(f"- `{path}`" for path in changed_files)

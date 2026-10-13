"""PR 제목·본문을 코드가 만든다 (plan.md Phase 33, docs/code/plan.md Q3). LLM은 쓰지 않는다."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

_TITLE = re.compile(r"PR\s*제목\s*[:：]\s*(\S[^\n]*)")
# `제목은 X 이라고 올려`처럼 문장 안에 든 제목. 끝의 `이라고/으로/로 + 동사`만 제목에서 뺀다.
_SENTENCE_TITLE = re.compile(
    r"제목\s*(?:은|는|을|를)\s+(\S[^\n]*?)"
    r"(?:\s*(?:이?라고|으로|로)\s*"
    r"(?:올려주세요|올려줘|올려|만들어줘|만들어|생성해줘|생성|해줘|해))?\s*$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class PullRequestDraft:
    title: str
    body: str


def build_pull_request(
    command: str, *, commit_subject: str, changed_files: Sequence[str]
) -> PullRequestDraft:
    match = _TITLE.search(command) or _SENTENCE_TITLE.search(command)
    return PullRequestDraft(
        title=match.group(1).strip() if match else commit_subject, body=_body(changed_files)
    )


def _body(changed_files: Sequence[str]) -> str:
    return "변경한 파일:\n" + "\n".join(f"- `{path}`" for path in changed_files)

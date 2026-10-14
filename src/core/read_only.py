"""사용자가 이 메시지에서 파일을 고치지 말라고 했는지 판정한다 (plan.md Phase 34).

코드가 쓰기를 막는 근거다. 이전 메시지의 표현은 이어지지 않고 현재 메시지만 본다.
"""

from __future__ import annotations

import re

_FORBIDS_EDIT = re.compile(
    r"(?:고치|수정|변경|건드리|바꾸|편집)\s*(?:하)?\s*지\s*(?:마|말)"
    r"|읽기\s*(?:만|전용)"
    r"|read[- ]?only"
    r"|(?:수정|변경)\s*없이",
    re.IGNORECASE,
)


def wants_read_only(message: str) -> bool:
    return _FORBIDS_EDIT.search(message) is not None

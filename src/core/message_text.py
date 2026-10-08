"""Plain text of a chat model reply.

Models served through the OpenAI Responses API return `AIMessage.content` as a
list of blocks instead of a string, so `str(content)` is not the reply text.
"""

from __future__ import annotations


def content_text(content: object) -> str:
    if isinstance(content, list):
        return "".join(
            block["text"]
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    return str(content)

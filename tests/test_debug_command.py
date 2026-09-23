"""POST /debug/command: manual testing entrypoint that routes through
dispatch_command (same shape v1's debug endpoint had).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.agent import FakeAnalysisAgent
from src.dispatch import render_result
from src.main import create_app
from src.thread_context import ThreadContextStore


def test_debug_command_routes_through_dispatch_command(tmp_path: Path) -> None:
    thread_context = ThreadContextStore(root=tmp_path / "context")
    agent = FakeAnalysisAgent()
    text = "이 프로젝트 뭐 하는 거야?"
    client = TestClient(create_app(thread_context=thread_context, agent=agent))

    response = client.post(
        "/debug/command",
        json={"channel_id": "C123", "thread_ts": "168000.0001", "text": text},
    )

    assert response.status_code == 200
    expected = render_result(agent.analyze(text))
    assert response.json()["response"] == expected

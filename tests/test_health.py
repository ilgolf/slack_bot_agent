"""The service exposes a health endpoint for readiness checks."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.main import create_app


def test_health_endpoint_reports_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

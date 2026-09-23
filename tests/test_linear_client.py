"""Linear GraphQL transport: failures are typed and secrets never reach logs."""

from __future__ import annotations

import logging

import httpx
import pytest

from src.linear_client import LinearApiError, LinearClient


def _client(handler: httpx.MockTransport) -> LinearClient:
    return LinearClient(api_key="lin_api_secret", client=httpx.Client(transport=handler))


def test_query_returns_data_and_sends_authorization_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "lin_api_secret"
        return httpx.Response(200, json={"data": {"teams": {"nodes": []}}})

    result = _client(httpx.MockTransport(handler)).query(operation="list_teams", document="query")

    assert result == {"teams": {"nodes": []}}


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(401, json={}), "요청이 실패"),
        (httpx.Response(200, json={"errors": [{"message": "nope"}]}), "처리하지 못"),
        (httpx.Response(200, content=b"not json"), "응답 형식"),
    ],
)
def test_query_converts_http_graphql_and_json_failures(
    response: httpx.Response, message: str
) -> None:
    client = _client(httpx.MockTransport(lambda request: response))

    with pytest.raises(LinearApiError, match=message):
        client.query(operation="test", document="query")


def test_query_does_not_log_api_key(caplog: pytest.LogCaptureFixture) -> None:
    client = _client(httpx.MockTransport(lambda request: httpx.Response(401, json={})))

    with caplog.at_level(logging.INFO), pytest.raises(LinearApiError):
        client.query(operation="test", document="query")

    assert "lin_api_secret" not in caplog.text


def test_blank_api_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="LINEAR_API_KEY"):
        LinearClient(api_key=" ")

"""Small, secret-safe transport boundary for Linear's GraphQL API."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class LinearApiError(RuntimeError):
    """A safe error category for an unsuccessful Linear API operation."""


class LinearClient:
    """Call one named, fixed GraphQL operation without logging request contents."""

    def __init__(
        self,
        *,
        api_key: str,
        api_url: str = "https://api.linear.app/graphql",
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("LINEAR_API_KEY is required")
        self._api_key = api_key
        self._api_url = api_url
        self._timeout_seconds = timeout_seconds
        self._client = client or httpx.Client()

    def query(
        self,
        *,
        operation: str,
        document: str,
        variables: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        """Return GraphQL data or raise ``LinearApiError`` on every failure shape.

        The caller supplies only module-owned operation documents.  Network and
        GraphQL error details are intentionally not surfaced, because those can
        include request data or workspace metadata.
        """
        try:
            response = self._client.post(
                self._api_url,
                headers={"Authorization": self._api_key},
                json={"query": document, "variables": dict(variables or {})},
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            logger.warning("linear_api_failed operation=%s category=timeout", operation)
            raise LinearApiError("Linear 요청 시간이 초과되었습니다.") from exc
        except httpx.HTTPError as exc:
            logger.warning("linear_api_failed operation=%s category=network", operation)
            raise LinearApiError("Linear 서버에 연결하지 못했습니다.") from exc

        if response.is_error:
            logger.warning(
                "linear_api_failed operation=%s category=http status=%s",
                operation,
                response.status_code,
            )
            raise LinearApiError("Linear API 요청이 실패했습니다.")
        try:
            payload = response.json()
        except ValueError as exc:
            logger.warning("linear_api_failed operation=%s category=invalid_json", operation)
            raise LinearApiError("Linear API 응답 형식이 올바르지 않습니다.") from exc
        if not isinstance(payload, dict):
            logger.warning("linear_api_failed operation=%s category=invalid_payload", operation)
            raise LinearApiError("Linear API 응답 형식이 올바르지 않습니다.")
        if payload.get("errors"):
            logger.warning("linear_api_failed operation=%s category=graphql", operation)
            raise LinearApiError("Linear API가 요청을 처리하지 못했습니다.")
        data = payload.get("data")
        if not isinstance(data, dict):
            logger.warning("linear_api_failed operation=%s category=missing_data", operation)
            raise LinearApiError("Linear API 응답에 결과가 없습니다.")
        logger.info("linear_api_completed operation=%s status=%s", operation, response.status_code)
        return data

"""The dashboard's only boundary to the Routing Engine: its public FastAPI API."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any

import httpx


@dataclass(frozen=True)
class ApiResult:
    status_code: int
    elapsed_ms: float
    payload: dict[str, Any]
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return 200 <= self.status_code < 300 and self.payload.get("success") is True


class RoutingEngineApiClient:
    """Small API client that never reaches into application internals or cloud providers."""

    def __init__(self, base_url: str, timeout: float = 90.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def chat(
        self, conversation_id: str, message: str, retry: bool = False, developer_mode: bool = False
    ) -> ApiResult:
        return self._request(
            "POST",
            "/chat",
            {"conversation_id": conversation_id, "message": message, "retry": retry},
            developer_mode,
        )

    def conversation(self, conversation_id: str) -> ApiResult:
        return self._request("GET", f"/conversation/{conversation_id}")

    def providers(self, developer_mode: bool = False) -> ApiResult:
        return self._request("GET", "/providers", developer_mode=developer_mode)

    def health(self) -> ApiResult:
        return self._request("GET", "/health")

    def router_health(self, developer_mode: bool = False) -> ApiResult:
        return self._request("GET", "/router/health", developer_mode=developer_mode)

    def provider_health(self) -> ApiResult:
        return self._request("GET", "/providers/health")

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None, developer_mode: bool = False
    ) -> ApiResult:
        started = perf_counter()
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.request(
                    method,
                    f"{self._base_url}{path}",
                    json=body,
                    headers={"X-Developer-Mode": "true"} if developer_mode else {},
                )
            payload = response.json()
            if not isinstance(payload, dict):
                payload = {}
            error = None if response.is_success else self._error_message(payload, response.reason_phrase)
            return ApiResult(response.status_code, (perf_counter() - started) * 1000, payload, error)
        except (httpx.HTTPError, ValueError) as exc:
            return ApiResult(0, (perf_counter() - started) * 1000, {}, "Unable to reach the Routing Engine API.")

    @staticmethod
    def _error_message(payload: dict[str, Any], fallback: str) -> str:
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        return fallback

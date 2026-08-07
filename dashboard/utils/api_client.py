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

    def policies(self, tenant_id: str) -> ApiResult:
        return self._request("GET", "/policies", api_version="v2", tenant_id=tenant_id)

    def create_policy(self, tenant_id: str, body: dict[str, Any]) -> ApiResult:
        return self._request("POST", "/policies", body, api_version="v2", tenant_id=tenant_id)

    def activate_policy(self, tenant_id: str, policy_id: str) -> ApiResult:
        return self._request("POST", f"/policies/{policy_id}/activate", api_version="v2", tenant_id=tenant_id)

    def evaluate_policy(self, tenant_id: str, body: dict[str, Any]) -> ApiResult:
        return self._request("POST", "/policies/evaluate", body, api_version="v2", tenant_id=tenant_id)

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        developer_mode: bool = False,
        api_version: str = "v1",
        tenant_id: str | None = None,
    ) -> ApiResult:
        started = perf_counter()
        try:
            headers = {"X-Developer-Mode": "true"} if developer_mode else {}
            if tenant_id:
                headers["X-Tenant-ID"] = tenant_id
            with httpx.Client(timeout=self._timeout) as client:
                response = client.request(
                    method,
                    f"{self._api_base(api_version)}{path}",
                    json=body,
                    headers=headers,
                )
            payload = response.json()
            if not isinstance(payload, dict):
                payload = {}
            error = None if response.is_success else self._error_message(payload, response.reason_phrase)
            return ApiResult(response.status_code, (perf_counter() - started) * 1000, payload, error)
        except (httpx.HTTPError, ValueError) as exc:
            return ApiResult(0, (perf_counter() - started) * 1000, {}, "Unable to reach the Routing Engine API.")

    def _api_base(self, api_version: str) -> str:
        suffix = "/api/v1"
        if self._base_url.endswith(suffix):
            return f"{self._base_url[:-len(suffix)]}/api/{api_version}"
        return self._base_url if api_version == "v1" else f"{self._base_url}/api/{api_version}"

    @staticmethod
    def _error_message(payload: dict[str, Any], fallback: str) -> str:
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        return fallback

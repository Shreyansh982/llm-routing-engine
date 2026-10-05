"""Structured-output Router integrations for configured cloud backends."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from pydantic import ValidationError

from config.settings import RouterBackendConfig
from core.interfaces import BaseRouter
from providers.adapter import classify_http_failure
from providers.circuit_breaker import BackendCircuits
from schemas.models import FailureScope, RouterDecision, RouterRequest, http_failure_reason


class RouterUnavailableError(RuntimeError):
    """A backend Router could not produce a usable decision."""

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        failure_reason: str = "UNKNOWN",
        failure_scope: FailureScope = FailureScope.NON_TRANSIENT,
        backend: str | None = None,
        blocks_backend: bool = False,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.failure_reason = failure_reason
        self.failure_scope = failure_scope
        self.backend = backend
        self.blocks_backend = blocks_backend


class StructuredHTTPRouter(BaseRouter):
    """OpenAI-compatible structured Router transport for a configured backend.

    OpenRouter and Groq both use the chat-completions request/response contract. Backend
    selection and credentials remain in ``RouterBackendConfig``; the Routing Engine only
    depends on this interface and never receives connection information.
    """

    def __init__(self, config: RouterBackendConfig, circuits: BackendCircuits) -> None:
        self._config = config
        self._circuits = circuits
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    async def decide(self, request: RouterRequest) -> RouterDecision:
        if not self._circuits.allow_request(self._config.backend):
            raise RouterUnavailableError(
                "Router backend circuit is open",
                failure_reason="CIRCUIT_OPEN",
                failure_scope=FailureScope.BACKEND_TRANSIENT,
                backend=self._config.backend,
            )
        client = await self._get_client()
        try:
            response = await client.post(
                self._config.endpoint,
                json=self._payload(request),
                headers=self._headers(),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            self._raise_unavailable(exc)
        try:
            decision = self._parse(response.json())
        except (ValueError, ValidationError, KeyError, IndexError, TypeError):
            decision = await self._repair(client, request)
        self._circuits.record_success(self._config.backend)
        return decision

    async def _repair(self, client: httpx.AsyncClient, request: RouterRequest) -> RouterDecision:
        try:
            response = await client.post(
                self._config.endpoint,
                json=self._payload(request, repair=True),
                headers=self._headers(),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            self._raise_unavailable(exc)
        try:
            return self._parse(response.json())
        except (ValueError, ValidationError, KeyError, IndexError, TypeError) as exc:
            self._circuits.record_failure(self._config.backend, False)
            raise RouterUnavailableError(
                "Router did not return a valid structured decision",
                failure_reason=self._failure_metadata(exc)[1],
            ) from exc

    def _raise_unavailable(self, error: Exception) -> None:
        http_status, failure_reason, failure_scope = self._failure_metadata(error)
        self._circuits.record_failure(
            self._config.backend, failure_scope == FailureScope.BACKEND_TRANSIENT
        )
        raise RouterUnavailableError(
            "Router did not return a valid structured decision",
            http_status=http_status,
            failure_reason=failure_reason,
            failure_scope=failure_scope,
            backend=self._config.backend,
            blocks_backend=http_status in {401, 403},
        ) from error

    async def health(self) -> str:
        try:
            client = await self._get_client()
            response = await client.get(self._models_endpoint(), headers=self._headers())
            response.raise_for_status()
            models = response.json()["data"]
            model_ids = {
                item["id"] for item in models if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
            return "healthy" if self._config.model in model_ids else "unhealthy"
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return "unhealthy"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._config.api_key.get_secret_value()}"}

    def _payload(self, request: RouterRequest, repair: bool = False) -> dict[str, Any]:
        instruction = (
            "Classify the request into exactly one available task group. "
            "Return a decision that conforms exactly to the supplied JSON schema. "
            "Never select or mention a provider, backend, model, priority, endpoint, or fallback chain. "
            "An image-bearing request must use multimodal. Use RETRY only when previous_response exists; "
            "use STOP only when no task group can be selected."
        )
        if repair:
            instruction += " Your previous response was invalid; return only schema-conformant JSON."
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": request.model_dump_json()},
            ],
            "stream": False,
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "router_decision",
                    "strict": True,
                    "schema": RouterDecision.model_json_schema(),
                },
            },
        }
        # Reasoning controls are backend-specific and omitted unless explicitly supported.
        if self._config.backend == "openrouter" and self._config.reasoning_effort is not None:
            payload["reasoning"] = {"effort": self._config.reasoning_effort}
        return payload

    async def _get_client(self) -> httpx.AsyncClient:
        """Reuse a connection pool for Router calls without changing routing behavior."""
        if self._client is None or getattr(self._client, "is_closed", False):
            async with self._client_lock:
                if self._client is None or getattr(self._client, "is_closed", False):
                    self._client = httpx.AsyncClient(timeout=self._config.timeout)
        return self._client

    @staticmethod
    def _parse(payload: Any) -> RouterDecision:
        content = payload["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("Router returned non-string content")
        return RouterDecision.model_validate(json.loads(content))

    @staticmethod
    def _failure_metadata(error: Exception) -> tuple[int | None, str, FailureScope]:
        if isinstance(error, httpx.HTTPStatusError):
            status = error.response.status_code
            return (
                status,
                http_failure_reason(status),
                classify_http_failure(status, error.response.text),
            )
        if isinstance(error, httpx.TimeoutException):
            return None, "TIMEOUT", FailureScope.BACKEND_TRANSIENT
        if isinstance(error, httpx.NetworkError):
            return None, "NETWORK_ERROR", FailureScope.BACKEND_TRANSIENT
        if isinstance(error, json.JSONDecodeError):
            return None, "INVALID_ROUTER_JSON", FailureScope.NON_TRANSIENT
        if isinstance(error, ValidationError):
            return None, "SCHEMA_VALIDATION_FAILED", FailureScope.NON_TRANSIENT
        if isinstance(error, (KeyError, IndexError, TypeError, ValueError)):
            return None, "INVALID_ROUTER_JSON", FailureScope.NON_TRANSIENT
        return None, "UNKNOWN", FailureScope.NON_TRANSIENT

    def _models_endpoint(self) -> str:
        suffix = "/chat/completions"
        if not self._config.endpoint.endswith(suffix):
            raise ValueError("Configured Router endpoint must end with /chat/completions")
        return f"{self._config.endpoint.removesuffix(suffix)}/models"

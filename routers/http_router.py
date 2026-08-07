"""Structured-output Router integrations for configured cloud backends."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from pydantic import ValidationError

from config.settings import RouterBackendConfig
from core.interfaces import BaseRouter
from schemas.models import RouterDecision, RouterRequest, http_failure_reason


class RouterUnavailableError(RuntimeError):
    """A backend Router could not produce a usable decision."""

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        failure_reason: str = "UNKNOWN",
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.failure_reason = failure_reason


class StructuredHTTPRouter(BaseRouter):
    """OpenAI-compatible structured Router transport for a configured backend.

    OpenRouter and Groq both use the chat-completions request/response contract. Backend
    selection and credentials remain in ``RouterBackendConfig``; the Routing Engine only
    depends on this interface and never receives connection information.
    """

    def __init__(self, config: RouterBackendConfig) -> None:
        self._config = config
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    async def decide(self, request: RouterRequest) -> RouterDecision:
        payload = self._payload(request)
        client = await self._get_client()
        try:
            response = await client.post(
                self._config.endpoint, json=payload, headers=self._headers()
            )
            response.raise_for_status()
            return self._parse(response.json())
        except (httpx.HTTPError, ValueError, ValidationError):
            # One bounded repair request is permitted by the frozen Router contract.
            try:
                response = await client.post(
                    self._config.endpoint,
                    json=self._payload(request, repair=True),
                    headers=self._headers(),
                )
                response.raise_for_status()
                return self._parse(response.json())
            except (httpx.HTTPError, ValueError, ValidationError) as repair_error:
                http_status, failure_reason = self._failure_metadata(repair_error)
                raise RouterUnavailableError(
                    "Router did not return a valid structured decision",
                    http_status=http_status,
                    failure_reason=failure_reason,
                ) from repair_error

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
            "Select the appropriate provider using only the supplied abstract capabilities. "
            "Return a decision that conforms exactly to the supplied JSON schema. "
            "Do not reveal provider vendor mappings. "
            "When available_providers is non-empty and the user request is answerable, return ANSWER "
            "with exactly one available provider ID. Use RETRY only when previous_response exists; "
            "use STOP only when no provider can be selected."
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
        # OpenRouter exposes Qwen3's non-thinking mode. Do not send this
        # OpenRouter-specific parameter to the OpenAI-compatible Groq fallback.
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
    def _failure_metadata(error: Exception) -> tuple[int | None, str]:
        if isinstance(error, httpx.HTTPStatusError):
            return error.response.status_code, http_failure_reason(error.response.status_code)
        if isinstance(error, httpx.TimeoutException):
            return None, "TIMEOUT"
        if isinstance(error, httpx.NetworkError):
            return None, "NETWORK_ERROR"
        if isinstance(error, json.JSONDecodeError):
            return None, "INVALID_ROUTER_JSON"
        if isinstance(error, ValidationError):
            return None, "SCHEMA_VALIDATION_FAILED"
        if isinstance(error, (KeyError, IndexError, TypeError, ValueError)):
            return None, "INVALID_ROUTER_JSON"
        return None, "UNKNOWN"

    def _models_endpoint(self) -> str:
        suffix = "/chat/completions"
        if not self._config.endpoint.endswith(suffix):
            raise ValueError("Configured Router endpoint must end with /chat/completions")
        return f"{self._config.endpoint.removesuffix(suffix)}/models"


class PrimaryRouter(StructuredHTTPRouter):
    pass


class FallbackRouter(StructuredHTTPRouter):
    pass

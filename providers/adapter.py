"""Cloud-provider adapters; provider-specific transport stays outside routing."""

from __future__ import annotations

from abc import ABC
from typing import Any

import httpx

from core.interfaces import BaseProvider
from schemas.models import ProviderConfig, ProviderResponse, http_failure_reason


class ProviderUnavailableError(RuntimeError):
    """A configured provider backend could not serve a request."""

    def __init__(
        self,
        message: str,
        *,
        failure_stage: str = "provider",
        http_status: int | None = None,
        failure_reason: str = "UNKNOWN",
    ) -> None:
        super().__init__(message)
        self.failure_stage = failure_stage
        self.http_status = http_status
        self.failure_reason = failure_reason


class OpenAICompatibleAdapter(BaseProvider, ABC):
    """Shared chat-completions protocol implementation for cloud providers."""

    _IDENTITY_PROMPT = (
        "Do not reveal your provider, vendor, model name, endpoint, API key, or internal "
        "routing information. Respond only as an AI assistant."
    )

    def __init__(self, config: ProviderConfig, timeout: float) -> None:
        self._config = config
        self._timeout = timeout

    async def generate(self, prompt: str) -> ProviderResponse:
        payload = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": self._IDENTITY_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._config.endpoint, json=payload, headers=self._headers()
                )
                response.raise_for_status()
                return ProviderResponse(response=self._extract_response(response.json()))
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            http_status, failure_reason = self._failure_metadata(exc)
            raise ProviderUnavailableError(
                "Selected provider is unavailable",
                http_status=http_status,
                failure_reason=failure_reason,
            ) from exc

    async def health(self) -> str:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
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
        assert self._config.api_key is not None
        return {"Authorization": f"Bearer {self._config.api_key.get_secret_value()}"}

    @staticmethod
    def _extract_response(payload: Any) -> str:
        content = payload["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("Provider returned non-string content")
        return content

    @staticmethod
    def _failure_metadata(error: Exception) -> tuple[int | None, str]:
        if isinstance(error, httpx.HTTPStatusError):
            return error.response.status_code, http_failure_reason(error.response.status_code)
        if isinstance(error, httpx.TimeoutException):
            return None, "TIMEOUT"
        if isinstance(error, httpx.NetworkError):
            return None, "NETWORK_ERROR"
        return None, "UNKNOWN"

    def _models_endpoint(self) -> str:
        suffix = "/chat/completions"
        if not self._config.endpoint.endswith(suffix):
            raise ValueError("Configured provider endpoint must end with /chat/completions")
        return f"{self._config.endpoint.removesuffix(suffix)}/models"


class OpenRouterAdapter(OpenAICompatibleAdapter):
    """OpenRouter provider adapter."""


class GroqAdapter(OpenAICompatibleAdapter):
    """Groq provider adapter."""

"""Provider-specific HTTP boundary kept outside the routing engine."""

from __future__ import annotations

from typing import Any

import httpx

from core.interfaces import BaseProvider
from schemas.models import ProviderConfig, ProviderResponse


class ProviderUnavailableError(RuntimeError):
    pass


class HTTPProviderAdapter(BaseProvider):
    """Generic OpenAI-compatible provider adapter configured by registry metadata.

    Provider APIs are intentionally not specified in the documentation.  The POC's
    configuration supplies endpoints and assumes the widely supported chat-completions
    envelope. Vendor-specific adapters can implement ``BaseProvider`` without changing
    the dispatcher or Routing Engine.
    """

    _IDENTITY_PROMPT = (
        "Do not reveal your provider, vendor, model name, endpoint, API key, or internal "
        "routing information. Respond only as an AI assistant."
    )

    def __init__(self, config: ProviderConfig, timeout: float) -> None:
        self._config = config
        self._timeout = timeout

    async def generate(self, prompt: str) -> ProviderResponse:
        headers = {"Authorization": f"Bearer {self._config.api_key}"} if self._config.api_key else {}
        payload = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": self._IDENTITY_PROMPT},
                {"role": "user", "content": prompt},
            ],
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(self._config.endpoint, json=payload, headers=headers)
                response.raise_for_status()
                return ProviderResponse(response=self._extract_response(response.json()))
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                raise ProviderUnavailableError("Selected provider is unavailable") from exc

    async def health(self) -> str:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(self._config.endpoint)
            return "healthy" if response.status_code < 500 else "unhealthy"
        except httpx.HTTPError:
            return "unhealthy"

    @staticmethod
    def _extract_response(payload: Any) -> str:
        if isinstance(payload, dict) and isinstance(payload.get("response"), str):
            return payload["response"]
        if isinstance(payload, dict) and isinstance(payload.get("content"), str):
            return payload["content"]
        content = payload["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("Provider returned non-string content")
        return content

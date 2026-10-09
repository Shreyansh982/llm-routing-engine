"""Cloud-provider adapters; provider-specific transport stays outside routing."""

from __future__ import annotations

from typing import Any

import httpx

from core.interfaces import BaseProvider
from providers.model_catalog import (
    ModelCatalogError,
    ModelVerificationTarget,
    load_model_catalog,
    resolve_catalog_model_id,
)
from schemas.models import (
    FailureScope,
    ProviderConfig,
    ProviderRequest,
    ProviderResponse,
    http_failure_reason,
)


def classify_http_failure(status: int, response_text: str) -> FailureScope:
    """Classify an upstream response without exposing its body outside transport code."""
    text = response_text.lower()
    model_specific = (
        "model unavailable",
        "model is unavailable",
        "model not found",
        "unsupported modality",
        "unsupported image",
        "unsupported request format",
    )
    if any(marker in text for marker in model_specific):
        return FailureScope.MODEL_SPECIFIC
    if status == 429:
        return FailureScope.MODEL_SPECIFIC
    if status >= 500:
        return FailureScope.BACKEND_TRANSIENT
    if status == 404:
        return FailureScope.MODEL_SPECIFIC
    return FailureScope.NON_TRANSIENT


class ProviderUnavailableError(RuntimeError):
    """A configured provider backend could not serve a request."""

    def __init__(
        self,
        message: str,
        *,
        failure_stage: str = "provider",
        http_status: int | None = None,
        failure_reason: str = "UNKNOWN",
        failure_scope: FailureScope = FailureScope.NON_TRANSIENT,
        blocks_backend: bool = False,
    ) -> None:
        super().__init__(message)
        self.failure_stage = failure_stage
        self.http_status = http_status
        self.failure_reason = failure_reason
        self.failure_scope = failure_scope
        self.blocks_backend = blocks_backend


class OpenAICompatibleAdapter(BaseProvider):
    """Shared chat-completions protocol implementation for cloud providers."""

    _IDENTITY_PROMPT = (
        "Do not reveal your provider, vendor, model name, endpoint, API key, or internal "
        "routing information. Respond only as an AI assistant."
    )

    def __init__(self, config: ProviderConfig, timeout: float) -> None:
        self._config = config
        self._timeout = timeout

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        payload = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": self._IDENTITY_PROMPT},
                {"role": "user", "content": self._user_content(request)},
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
            http_status, failure_reason, failure_scope, blocks_backend = self._failure_metadata(exc)
            raise ProviderUnavailableError(
                "Selected provider is unavailable",
                http_status=http_status,
                failure_reason=failure_reason,
                failure_scope=failure_scope,
                blocks_backend=blocks_backend,
            ) from exc

    async def health(self) -> str:
        try:
            catalog_ids = await load_model_catalog(
                ModelVerificationTarget(
                    name=self._config.id,
                    backend=self._config.backend,
                    endpoint=self._config.endpoint,
                    model=self._config.model,
                    api_key=self._config.api_key,
                    timeout=self._timeout,
                )
            )
            match = resolve_catalog_model_id(
                self._config.backend, self._config.model, catalog_ids
            )
            return "healthy" if match is not None else "unhealthy"
        except ModelCatalogError:
            return "unhealthy"

    def _headers(self) -> dict[str, str]:
        assert self._config.api_key is not None
        return {"Authorization": f"Bearer {self._config.api_key.get_secret_value()}"}

    @staticmethod
    def _user_content(request: ProviderRequest) -> str | list[dict[str, Any]]:
        if request.image is None:
            return request.text
        return [
            {"type": "text", "text": request.text},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{request.image.media_type};base64,{request.image.data_base64}"
                },
            },
        ]

    @staticmethod
    def _extract_response(payload: Any) -> str:
        content = payload["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("Provider returned non-string content")
        return content

    @staticmethod
    def _failure_metadata(
        error: Exception,
    ) -> tuple[int | None, str, FailureScope, bool]:
        if isinstance(error, httpx.HTTPStatusError):
            status = error.response.status_code
            scope = classify_http_failure(status, error.response.text)
            return status, http_failure_reason(status), scope, status in {401, 403}
        if isinstance(error, httpx.TimeoutException):
            return None, "TIMEOUT", FailureScope.BACKEND_TRANSIENT, False
        if isinstance(error, httpx.NetworkError):
            return None, "NETWORK_ERROR", FailureScope.BACKEND_TRANSIENT, False
        return None, "INVALID_PROVIDER_RESPONSE", FailureScope.MODEL_SPECIFIC, False

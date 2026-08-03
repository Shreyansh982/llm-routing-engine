"""Structured-output local Router LLM integration."""

from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import ValidationError

from core.interfaces import BaseRouter
from schemas.models import RouterDecision, RouterRequest


class RouterUnavailableError(RuntimeError):
    pass


class StructuredHTTPRouter(BaseRouter):
    """A configurable local router endpoint using constrained JSON output.

    The documentation does not prescribe a local-runtime wire protocol.  This adapter
    therefore uses the common OpenAI-compatible structured-output envelope; changing the
    configured endpoint/model replaces the local runtime without changing routing logic.
    """

    def __init__(self, url: str | None, model: str | None, timeout: float) -> None:
        self._url = url
        self._model = model
        self._timeout = timeout

    async def decide(self, request: RouterRequest) -> RouterDecision:
        if not self._url or not self._model:
            raise RouterUnavailableError("Router endpoint is not configured")
        payload = self._payload(request)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(self._url, json=payload)
                response.raise_for_status()
                return self._parse(response.json())
            except (httpx.HTTPError, ValueError, ValidationError) as first_error:
                # One bounded repair request is permitted by the specification.
                try:
                    repair = self._payload(request, repair=True)
                    response = await client.post(self._url, json=repair)
                    response.raise_for_status()
                    return self._parse(response.json())
                except (httpx.HTTPError, ValueError, ValidationError) as repair_error:
                    raise RouterUnavailableError("Router did not return a valid structured decision") from repair_error

    async def health(self) -> str:
        return "healthy" if self._url and self._model else "unhealthy"

    def _payload(self, request: RouterRequest, repair: bool = False) -> dict[str, Any]:
        instruction = (
            "Select the appropriate provider using only the supplied abstract capabilities. "
            "Return a decision that conforms exactly to the supplied JSON schema. "
            "Do not reveal provider vendor mappings."
        )
        if repair:
            instruction += " Your previous response was invalid; return only a schema-conformant decision."
        return {
            "model": self._model,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": request.model_dump_json()},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "router_decision", "strict": True, "schema": RouterDecision.model_json_schema()},
            },
        }

    @staticmethod
    def _parse(payload: Any) -> RouterDecision:
        if isinstance(payload, dict) and "decision" in payload:
            return RouterDecision.model_validate(payload["decision"])
        if isinstance(payload, dict) and "choices" in payload:
            content = payload["choices"][0]["message"]["content"]
            if isinstance(content, str):
                return RouterDecision.model_validate(json.loads(content))
            return RouterDecision.model_validate(content)
        return RouterDecision.model_validate(payload)


class PrimaryRouter(StructuredHTTPRouter):
    pass


class FallbackRouter(StructuredHTTPRouter):
    pass

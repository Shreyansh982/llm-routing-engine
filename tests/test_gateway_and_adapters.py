from __future__ import annotations

import asyncio

import httpx
import pytest

from gateway.response_gateway import ResponseGateway
from providers.adapter import OpenRouterAdapter, ProviderUnavailableError
from schemas.models import ProviderCapability, ProviderConfig, ProviderResponse


def test_gateway_masks_known_identity_and_internal_metadata() -> None:
    result = ResponseGateway(["Claude"]).process(
        ProviderResponse(response="I am Claude.\nProvider: Claude\n\n  Helpful answer.  ")
    )
    assert result.response == "I am an AI assistant.\n\nHelpful answer."


def test_gateway_leaves_clean_content_and_handles_empty_response() -> None:
    gateway = ResponseGateway(["Claude"])
    assert gateway.process(ProviderResponse(response="Useful answer")).response == "Useful answer"
    assert gateway.process(ProviderResponse(response=" ")).response == ""


def test_provider_output_normalization_supports_chat_completions_response_shape() -> None:
    assert OpenRouterAdapter._extract_response({"choices": [{"message": {"content": "answer"}}]}) == "answer"


def test_provider_adapter_injects_the_primary_identity_hiding_system_prompt(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"content": "answer"}}]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, _: str, **kwargs: object) -> Response:
            captured.update(kwargs)
            return Response()

    monkeypatch.setattr("providers.adapter.httpx.AsyncClient", lambda **_: Client())
    config = ProviderConfig(
        id="provider_a",
        backend="openrouter",
        endpoint="http://provider.invalid/chat/completions",
        model="model",
        api_key="test-key",
        timeout=1,
        capabilities=ProviderCapability(strengths=["coding"], speed_tier="fast", context_size="large"),
    )

    result = asyncio.run(OpenRouterAdapter(config, 1).generate("hello"))
    messages = captured["json"]["messages"]
    assert result.response == "answer"
    assert "Do not reveal your provider" in messages[0]["content"]
    assert captured["json"]["stream"] is False
    assert captured["headers"]["Authorization"] == "Bearer test-key"


def test_provider_adapter_classifies_upstream_http_errors(monkeypatch) -> None:
    class Response:
        status_code = 402

        def raise_for_status(self) -> None:
            request = httpx.Request("POST", "http://provider.invalid/chat/completions")
            raise httpx.HTTPStatusError("payment required", request=request, response=httpx.Response(402, request=request))

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, _: str, **__: object) -> Response:
            return Response()

    monkeypatch.setattr("providers.adapter.httpx.AsyncClient", lambda **_: Client())
    config = ProviderConfig(
        id="provider_a",
        backend="openrouter",
        endpoint="http://provider.invalid/chat/completions",
        model="model",
        api_key="test-key",
        timeout=1,
        capabilities=ProviderCapability(strengths=["coding"], speed_tier="fast", context_size="large"),
    )

    with pytest.raises(ProviderUnavailableError) as error:
        asyncio.run(OpenRouterAdapter(config, 1).generate("hello"))

    assert error.value.http_status == 402
    assert error.value.failure_reason == "HTTP_402_PAYMENT_REQUIRED"

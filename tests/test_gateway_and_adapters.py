from __future__ import annotations

import asyncio
import base64

import httpx
import pytest

from gateway.response_gateway import ResponseGateway
from providers.adapter import OpenAICompatibleAdapter, ProviderUnavailableError
from schemas.models import ProviderCapability, ProviderConfig, ProviderRequest, ProviderResponse
from tests.conftest import PNG_BASE64


def test_gateway_masks_self_identity_and_preserves_other_content() -> None:
    result = ResponseGateway(["Claude"]).process(
        ProviderResponse(response="I am Claude.\nProvider: Claude\n\n  Helpful answer.  ")
    )
    assert result.response == "I am an AI assistant.\nProvider: Claude\n\n  Helpful answer.  "


def test_gateway_leaves_clean_content_and_handles_empty_response() -> None:
    gateway = ResponseGateway(["Claude"])
    assert gateway.process(ProviderResponse(response="Useful answer")).response == "Useful answer"
    assert gateway.process(ProviderResponse(response=" ")).response == " "


def test_provider_output_normalization_supports_chat_completions_response_shape() -> None:
    assert OpenAICompatibleAdapter._extract_response({"choices": [{"message": {"content": "answer"}}]}) == "answer"


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
        task_group="general",
        priority=1,
        api_key="test-key",
        timeout=1,
        capabilities=ProviderCapability(strengths=["coding"], speed_tier="fast", context_size="large"),
    )

    result = asyncio.run(OpenAICompatibleAdapter(config, 1).generate(ProviderRequest(text="hello")))
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
        task_group="general",
        priority=1,
        api_key="test-key",
        timeout=1,
        capabilities=ProviderCapability(strengths=["coding"], speed_tier="fast", context_size="large"),
    )

    with pytest.raises(ProviderUnavailableError) as error:
        asyncio.run(OpenAICompatibleAdapter(config, 1).generate(ProviderRequest(text="hello")))

    assert error.value.http_status == 402
    assert error.value.failure_reason == "HTTP_402_PAYMENT_REQUIRED"


def test_provider_adapter_encodes_image_only_at_transport_boundary(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"content": "description"}}]}

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
        id="image_provider",
        backend="nvidia",
        endpoint="http://provider.invalid/chat/completions",
        model="image-model",
        task_group="multimodal",
        priority=1,
        api_key="test-key",
        timeout=1,
        capabilities=ProviderCapability(
            strengths=["vision"],
            speed_tier="fast",
            context_size="large",
            input_modalities=["text", "image"],
        ),
    )
    encoded = PNG_BASE64
    asyncio.run(
        OpenAICompatibleAdapter(config, 1).generate(
            ProviderRequest(
                text="describe",
                image={"media_type": "image/png", "data_base64": encoded},
            )
        )
    )
    content = captured["json"]["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "describe"}
    assert content[1]["image_url"]["url"] == f"data:image/png;base64,{encoded}"


def test_provider_error_does_not_expose_upstream_body_or_credentials(monkeypatch) -> None:
    secret = "upstream-secret-body"

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, _: str, **__: object):
            request = httpx.Request("POST", "http://provider.invalid/chat/completions")
            response = httpx.Response(401, text=secret, request=request)
            raise httpx.HTTPStatusError(secret, request=request, response=response)

    monkeypatch.setattr("providers.adapter.httpx.AsyncClient", lambda **_: Client())
    config = ProviderConfig(
        id="provider_a",
        backend="openrouter",
        endpoint="http://provider.invalid/chat/completions",
        model="model",
        task_group="general",
        priority=1,
        api_key="credential-value",
        timeout=1,
        capabilities=ProviderCapability(strengths=["general"], speed_tier="fast", context_size="large"),
    )
    with pytest.raises(ProviderUnavailableError) as error:
        asyncio.run(OpenAICompatibleAdapter(config, 1).generate(ProviderRequest(text="hello")))
    assert secret not in str(error.value)
    assert "credential-value" not in str(error.value)
    assert error.value.blocks_backend is True

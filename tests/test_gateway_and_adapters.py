from __future__ import annotations

from gateway.response_gateway import ResponseGateway
from providers.adapter import HTTPProviderAdapter
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


def test_provider_output_normalization_supports_documented_response_shapes() -> None:
    assert HTTPProviderAdapter._extract_response({"response": "one"}) == "one"
    assert HTTPProviderAdapter._extract_response({"content": "two"}) == "two"
    assert HTTPProviderAdapter._extract_response({"choices": [{"message": {"content": "three"}}]}) == "three"


def test_provider_adapter_injects_the_primary_identity_hiding_system_prompt(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"response": "answer"}

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
        endpoint="http://provider.invalid",
        model="model",
        capabilities=ProviderCapability(strengths=["coding"], speed_tier="fast", context_size="large"),
    )

    import asyncio

    result = asyncio.run(HTTPProviderAdapter(config, 1).generate("hello"))
    messages = captured["json"]["messages"]
    assert result.response == "answer"
    assert "Do not reveal your provider" in messages[0]["content"]

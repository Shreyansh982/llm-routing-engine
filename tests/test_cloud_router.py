from __future__ import annotations

import asyncio
import json

from config.settings import RouterBackendConfig
from routers.http_router import PrimaryRouter
from schemas.models import AvailableProvider, ProviderCapability, RouterRequest


def test_router_uses_backend_chat_schema_mode_and_parses_structured_decision(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {"message": {"content": json.dumps(
                        {
                            "action": "ANSWER",
                            "selected_provider": "provider_a",
                            "confidence": 0.9,
                            "reason": "coding capability",
                        }
                    )}}
                ]
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, _: str, **kwargs: object) -> Response:
            captured.update(kwargs)
            return Response()

    monkeypatch.setattr("routers.http_router.httpx.AsyncClient", lambda **_: Client())
    request = RouterRequest(
        original_query="Write a function",
        latest_user_message="Write a function",
        attempt=0,
        max_attempts=3,
        available_providers=[
            AvailableProvider(
                id="provider_a",
                capabilities=ProviderCapability(
                    strengths=["coding"], speed_tier="standard", context_size="large"
                ),
            )
        ],
    )

    config = RouterBackendConfig(
        backend="openrouter",
        endpoint="http://backend.invalid/chat/completions",
        model="router",
        api_key="test-key",
        timeout=1,
    )
    decision = asyncio.run(PrimaryRouter(config).decide(request))

    assert decision.selected_provider == "provider_a"
    assert captured["json"]["stream"] is False
    assert captured["json"]["response_format"]["type"] == "json_schema"
    assert captured["headers"]["Authorization"] == "Bearer test-key"

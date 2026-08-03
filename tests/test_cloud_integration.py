"""Optional live OpenRouter/Groq integration checks, enabled explicitly in CI or development."""

from __future__ import annotations

import asyncio
import os

import pytest
from fastapi.testclient import TestClient

from api.main import build_engine, create_app
from config.settings import Settings
from schemas.models import ChatRequest, RouterRequest

pytestmark = pytest.mark.cloud


@pytest.mark.skipif(
    os.getenv("RUN_CLOUD_INTEGRATION") != "1",
    reason="Set RUN_CLOUD_INTEGRATION=1 with valid configured cloud credentials.",
)
def test_configured_cloud_routers_providers_and_end_to_end_flow() -> None:
    settings = Settings()
    engine = build_engine(settings)
    registry = engine._registry

    with TestClient(create_app(settings=settings)):
        pass

    request = RouterRequest(
        original_query="Write a short Python function.",
        latest_user_message="Write a short Python function.",
        attempt=0,
        max_attempts=settings.max_retries,
        available_providers=registry.available_for_router([]),
    )
    decision = asyncio.run(engine._primary.decide(request))
    assert engine._validator.validate(decision, []).selected_provider in {
        provider.id for provider in registry.list_providers()
    }

    for provider in registry.list_providers():
        response = asyncio.run(engine._dispatcher.dispatch(provider.id, "Reply with a short greeting."))
        assert response.response.strip()

    result = asyncio.run(
        engine.handle(ChatRequest(conversation_id="cloud-integration", message="Explain clean architecture."))
    )
    assert result.action == "ANSWER" and result.response.strip()

"""Optional four-backend integration checks, enabled explicitly in CI or development."""

from __future__ import annotations

import asyncio
import os
from time import perf_counter

import pytest
from fastapi.testclient import TestClient

from api.main import build_engine, create_app
from config.settings import Settings
from providers.adapter import ProviderUnavailableError
from routers.http_router import RouterUnavailableError
from schemas.models import ProviderRequest, RouterRequest

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
        available_task_groups=registry.available_task_groups(),
    )
    operational_failures: list[str] = []
    operational_statuses = {402, 429, 500, 502, 503, 504}
    for name, router in (("google", engine._primary), ("nvidia", engine._fallback)):
        started = perf_counter()
        try:
            decision = asyncio.run(router.decide(request))
        except RouterUnavailableError as exc:
            if exc.http_status in operational_statuses or exc.failure_reason in {
                "TIMEOUT",
                "NETWORK_ERROR",
            }:
                operational_failures.append(
                    f"{name} router: {exc.failure_reason}"
                )
                continue
            raise
        assert engine._validator.validate(
            decision, registry.available_task_groups(), False
        ).task_group
        assert perf_counter() - started <= settings.router_timeout + 1

    assert engine._fallback._config.model == "nvidia/nemotron-3.5-lightning-30b-a3b"
    attempted_backends: set[str] = set()
    for backend in ("google", "nvidia", "openrouter", "groq"):
        provider = next(item for item in registry.list_providers() if item.backend == backend)
        attempted_backends.add(backend)
        try:
            response = asyncio.run(
                engine._dispatcher.dispatch(
                    provider.id, ProviderRequest(text="Reply with a short greeting.")
                )
            )
        except ProviderUnavailableError as exc:
            if exc.http_status in operational_statuses or exc.failure_reason in {
                "TIMEOUT",
                "NETWORK_ERROR",
            }:
                operational_failures.append(
                    f"{backend} task model: {exc.failure_reason}"
                )
                continue
            raise
        assert response.response.strip()
    assert attempted_backends == {"google", "nvidia", "openrouter", "groq"}
    if operational_failures:
        pytest.xfail("; ".join(operational_failures))

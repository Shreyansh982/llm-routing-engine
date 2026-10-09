from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from config.settings import RouterBackendConfig
from providers.circuit_breaker import BackendCircuits
from routers.http_router import StructuredHTTPRouter
from routing.engine import RoutingEngineError
from schemas.models import ChatRequest
from tests.conftest import FakeProvider, make_engine


class ValidDecisionResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "action": "ANSWER",
                                "task_group": "general",
                                "confidence": 0.9,
                                "reason": "general request",
                            }
                        )
                    }
                }
            ]
        }


class HttpFailureClient:
    def __init__(self, status: int) -> None:
        self.status = status
        self.calls = 0

    async def post(self, url: str, **_: object) -> ValidDecisionResponse:
        self.calls += 1
        request = httpx.Request("POST", url)
        response = httpx.Response(self.status, request=request)
        raise httpx.HTTPStatusError("router failure", request=request, response=response)


class SlowClient:
    def __init__(self) -> None:
        self.calls = 0

    async def post(self, _: str, **__: object) -> ValidDecisionResponse:
        self.calls += 1
        await asyncio.sleep(1)
        return ValidDecisionResponse()


class SuccessClient:
    def __init__(self) -> None:
        self.calls = 0

    async def post(self, _: str, **__: object) -> ValidDecisionResponse:
        self.calls += 1
        return ValidDecisionResponse()


class MalformedClient:
    def __init__(self) -> None:
        self.calls = 0

    async def post(self, _: str, **__: object) -> object:
        self.calls += 1

        class Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {"choices": [{"message": {"content": "not-json"}}]}

        return Response()


def router(backend: str, timeout: float, circuits: BackendCircuits) -> StructuredHTTPRouter:
    model = (
        "gemini-3.8-flash"
        if backend == "google"
        else "nvidia/nemotron-3.5-lightning-30b-a3b"
    )
    return StructuredHTTPRouter(
        RouterBackendConfig(
            backend=backend,
            endpoint=f"http://{backend}.invalid/chat/completions",
            model=model,
            api_key="test-key",
            timeout=timeout,
            temperature=0,
            max_tokens=128,
            reasoning_effort="low",
        ),
        circuits,
    )


def client_factory(monkeypatch, clients: list[object]) -> list[float]:
    timeouts: list[float] = []

    def factory(**kwargs: object) -> object:
        timeouts.append(float(kwargs["timeout"]))
        return clients.pop(0)

    monkeypatch.setattr("routers.http_router.httpx.AsyncClient", factory)
    return timeouts


def test_google_503_moves_immediately_to_nemotron_without_retry(monkeypatch) -> None:
    google_client = HttpFailureClient(503)
    nvidia_client = SuccessClient()
    timeouts = client_factory(monkeypatch, [google_client, nvidia_client])
    circuits = BackendCircuits()
    engine, _, _ = make_engine(router("google", 0.05, circuits), router("nvidia", 0.05, circuits))
    FakeProvider.responses = {"provider_a": "answer"}

    result = asyncio.run(
        engine.handle(
            ChatRequest(conversation_id="google-503", message="hello"),
            developer_mode=True,
        )
    )

    assert result.response == "answer"
    assert result.diagnostics is not None
    assert result.diagnostics.router_used == "fallback_router"
    assert result.diagnostics.router_attempts[0]["failure_reason"] == "HTTP_503_PROVIDER_UNAVAILABLE"
    assert google_client.calls == 1
    assert nvidia_client.calls == 1
    assert timeouts == [0.05, 0.05]


def test_google_timeout_moves_to_nemotron_within_router_budget(monkeypatch) -> None:
    google_client = SlowClient()
    nvidia_client = SuccessClient()
    client_factory(monkeypatch, [google_client, nvidia_client])
    circuits = BackendCircuits()
    engine, _, _ = make_engine(router("google", 0.02, circuits), router("nvidia", 0.02, circuits))
    FakeProvider.responses = {"provider_a": "answer"}

    started = time.perf_counter()
    result = asyncio.run(
        engine.handle(
            ChatRequest(conversation_id="google-timeout", message="hello"),
            developer_mode=True,
        )
    )
    elapsed = time.perf_counter() - started

    assert result.diagnostics is not None
    assert result.diagnostics.router_used == "fallback_router"
    assert result.diagnostics.router_attempts[0]["failure_reason"] == "TIMEOUT"
    assert google_client.calls == 1 and nvidia_client.calls == 1
    assert 0.015 <= elapsed < 0.1


def test_google_503_and_slow_nemotron_end_in_bounded_controlled_failure(monkeypatch) -> None:
    google_client = HttpFailureClient(503)
    nvidia_client = SlowClient()
    client_factory(monkeypatch, [google_client, nvidia_client])
    router_timeout = 0.02
    circuits = BackendCircuits()
    engine, _, _ = make_engine(
        router("google", router_timeout, circuits),
        router("nvidia", router_timeout, circuits),
    )

    started = time.perf_counter()
    with pytest.raises(RoutingEngineError) as error:
        asyncio.run(
            engine.handle(
                ChatRequest(conversation_id="dual-failure", message="hello"),
                developer_mode=True,
            )
        )
    elapsed = time.perf_counter() - started

    assert error.value.diagnostics is not None
    assert [item["failure_reason"] for item in error.value.diagnostics.router_attempts] == [
        "HTTP_503_PROVIDER_UNAVAILABLE",
        "TIMEOUT",
    ]
    assert google_client.calls == 1 and nvidia_client.calls == 1
    assert router_timeout * 0.75 <= elapsed < router_timeout * 3


def test_failed_google_repair_moves_to_nemotron_after_exactly_two_google_calls(
    monkeypatch,
) -> None:
    google_client = MalformedClient()
    nvidia_client = SuccessClient()
    client_factory(monkeypatch, [google_client, nvidia_client])
    circuits = BackendCircuits()
    engine, _, _ = make_engine(router("google", 0.05, circuits), router("nvidia", 0.05, circuits))
    FakeProvider.responses = {"provider_a": "answer"}

    result = asyncio.run(
        engine.handle(
            ChatRequest(conversation_id="repair-fallback", message="hello"),
            developer_mode=True,
        )
    )

    assert result.diagnostics is not None
    assert result.diagnostics.router_used == "fallback_router"
    assert google_client.calls == 2
    assert nvidia_client.calls == 1

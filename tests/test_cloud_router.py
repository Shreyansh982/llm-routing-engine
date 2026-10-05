from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from config.settings import RouterBackendConfig
from providers.circuit_breaker import BackendCircuits
from routers.http_router import RouterUnavailableError, StructuredHTTPRouter
from schemas.models import RouterRequest


def test_router_uses_backend_chat_schema_mode_and_parses_structured_decision(monkeypatch) -> None:
    captured: dict[str, object] = {}
    created = {"count": 0}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {"message": {"content": json.dumps(
                        {
                            "action": "ANSWER",
                            "task_group": "coding_reasoning",
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

    def client_factory(**_: object) -> Client:
        created["count"] += 1
        return Client()

    monkeypatch.setattr("routers.http_router.httpx.AsyncClient", client_factory)
    request = RouterRequest(
        original_query="Write a function",
        latest_user_message="Write a function",
        attempt=0,
        max_attempts=3,
        available_task_groups=["general", "coding_reasoning"],
    )

    config = RouterBackendConfig(
        backend="openrouter",
        endpoint="http://backend.invalid/chat/completions",
        model="router",
        api_key="test-key",
        timeout=1,
    )
    router = StructuredHTTPRouter(config, BackendCircuits())

    async def decide_twice() -> tuple[object, object]:
        return await router.decide(request), await router.decide(request)

    decision, repeated_decision = asyncio.run(decide_twice())

    assert decision.task_group == "coding_reasoning"
    assert repeated_decision.task_group == "coding_reasoning"
    assert created["count"] == 1
    assert captured["json"]["stream"] is False
    assert captured["json"]["temperature"] == 0
    assert captured["json"]["max_tokens"] == 96
    assert captured["json"]["reasoning"] == {"effort": "none"}
    assert captured["json"]["response_format"]["type"] == "json_schema"
    assert captured["headers"]["Authorization"] == "Bearer test-key"


def test_malformed_success_gets_exactly_one_repair_request(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class Response:
        def __init__(self, content: str) -> None:
            self.content = content

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"content": self.content}}]}

    responses = [Response("not-json"), Response(json.dumps({
        "action": "ANSWER",
        "task_group": "general",
        "confidence": 0.9,
        "reason": "repaired",
    }))]

    class Client:
        async def post(self, _: str, **kwargs: object) -> Response:
            calls.append(kwargs)
            return responses.pop(0)

    monkeypatch.setattr("routers.http_router.httpx.AsyncClient", lambda **_: Client())
    router = StructuredHTTPRouter(_router_config(), BackendCircuits())
    decision = asyncio.run(router.decide(_router_request()))
    assert decision.task_group == "general"
    assert len(calls) == 2
    assert "previous response was invalid" in calls[1]["json"]["messages"][0]["content"]


def test_transport_failure_is_not_retried_against_same_router(monkeypatch) -> None:
    calls = 0

    class Client:
        async def post(self, _: str, **__: object):
            nonlocal calls
            calls += 1
            request = httpx.Request("POST", "http://backend.invalid/chat/completions")
            response = httpx.Response(429, request=request)
            raise httpx.HTTPStatusError("rate limited", request=request, response=response)

    monkeypatch.setattr("routers.http_router.httpx.AsyncClient", lambda **_: Client())
    router = StructuredHTTPRouter(_router_config(), BackendCircuits())
    with pytest.raises(RouterUnavailableError) as error:
        asyncio.run(router.decide(_router_request()))
    assert calls == 1
    assert error.value.http_status == 429


@pytest.mark.parametrize("status", [401, 403])
def test_router_auth_failure_blocks_request_backend_without_opening_circuit(
    monkeypatch, status: int
) -> None:
    class Client:
        async def post(self, _: str, **__: object):
            request = httpx.Request("POST", "http://backend.invalid/chat/completions")
            response = httpx.Response(status, request=request)
            raise httpx.HTTPStatusError("auth", request=request, response=response)

    monkeypatch.setattr("routers.http_router.httpx.AsyncClient", lambda **_: Client())
    circuits = BackendCircuits(failure_threshold=1)
    router = StructuredHTTPRouter(_router_config(), circuits)
    with pytest.raises(RouterUnavailableError) as error:
        asyncio.run(router.decide(_router_request()))
    assert error.value.blocks_backend is True
    assert error.value.backend == "google"
    assert circuits.snapshot()["google"]["state"] == "CLOSED"


def test_failed_repair_is_bounded_to_two_requests(monkeypatch) -> None:
    calls = 0

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"content": "not-json"}}]}

    class Client:
        async def post(self, _: str, **__: object) -> Response:
            nonlocal calls
            calls += 1
            return Response()

    monkeypatch.setattr("routers.http_router.httpx.AsyncClient", lambda **_: Client())
    router = StructuredHTTPRouter(_router_config(), BackendCircuits())
    with pytest.raises(RouterUnavailableError, match="valid structured decision"):
        asyncio.run(router.decide(_router_request()))
    assert calls == 2


def _router_config() -> RouterBackendConfig:
    return RouterBackendConfig(
        backend="google",
        endpoint="http://backend.invalid/chat/completions",
        model="gemini-3.8-flash",
        api_key="test-key",
        timeout=1,
    )


def _router_request() -> RouterRequest:
    return RouterRequest(
        original_query="hello",
        latest_user_message="hello",
        attempt=0,
        max_attempts=3,
        available_task_groups=["general"],
    )

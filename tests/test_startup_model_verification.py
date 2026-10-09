from __future__ import annotations

import asyncio
from collections import Counter

import httpx
import pytest
from pydantic import SecretStr

from providers.model_catalog import (
    ModelVerificationTarget,
    StartupModelVerifier,
    resolve_catalog_model_id,
)
from routing.engine import RoutingEngineError
from tests.conftest import QueueRouter, make_engine


CATALOGS = {
    "google": {
        "models/gemini-3.8-flash",
        "models/gemini-3.5-flash-lite",
        "models/gemini-3.6-flash",
    },
    "nvidia": {
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        "deepseek-ai/deepseek-v4.1-flash",
        "moonshotai/kimi-k3",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    },
    "openrouter": {
        "qwen/qwen3.8-27b",
        "cohere/north-mini-code:free",
        "poolside/laguna-s-2.1:free",
        "google/gemma-4-31b-it:free",
        "google/gemma-4-26b-a4b-it:free",
    },
    "groq": {"openai/gpt-oss-20b", "openai/gpt-oss-120b"},
}


def target(name: str, backend: str, model: str, role: str = "provider") -> ModelVerificationTarget:
    return ModelVerificationTarget(
        name=name,
        backend=backend,
        endpoint=f"https://{backend}.invalid/chat/completions",
        model=model,
        api_key=SecretStr("test-key"),
        timeout=1,
        role=role,
    )


def configured_targets() -> list[ModelVerificationTarget]:
    return [
        target("primary_router", "google", "gemini-3.8-flash", "router"),
        target(
            "fallback_router",
            "nvidia",
            "nvidia/nemotron-3.5-lightning-30b-a3b",
            "router",
        ),
        target("g1_google_flash_lite", "google", "gemini-3.5-flash-lite"),
        target("g2_openrouter_qwen", "openrouter", "qwen/qwen3.8-27b"),
        target("g3_groq_oss20b", "groq", "openai/gpt-oss-20b"),
        target("g4_nvidia_deepseek", "nvidia", "deepseek-ai/deepseek-v4.1-flash"),
        target("c1_groq_oss120b", "groq", "openai/gpt-oss-120b"),
        target("c2_nvidia_kimi", "nvidia", "moonshotai/kimi-k3"),
        target("c3_openrouter_north", "openrouter", "cohere/north-mini-code:free"),
        target("c4_openrouter_laguna", "openrouter", "poolside/laguna-s-2.1:free"),
        target("w1_openrouter_gemma31b", "openrouter", "google/gemma-4-31b-it:free"),
        target("w2_google_flash", "google", "gemini-3.6-flash"),
        target(
            "m1_nvidia_nemotron_omni",
            "nvidia",
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        ),
        target("m2_openrouter_gemma26b", "openrouter", "google/gemma-4-26b-a4b-it:free"),
    ]


def response(status: int, payload: object, url: str) -> httpx.Response:
    return httpx.Response(status, json=payload, request=httpx.Request("GET", url))


def test_startup_fetches_each_backend_catalog_once_and_resolves_all_configured_models(
    monkeypatch, caplog
) -> None:
    calls: Counter[str] = Counter()

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, url: str, **_: object) -> httpx.Response:
            backend = url.split("//", 1)[1].split(".", 1)[0]
            calls[backend] += 1
            return response(200, {"data": [{"id": item} for item in CATALOGS[backend]]}, url)

    monkeypatch.setattr("providers.model_catalog.httpx.AsyncClient", lambda **_: Client())
    engine, _, _ = make_engine(QueueRouter([]), QueueRouter([]))
    engine._startup_model_verifier = StartupModelVerifier(configured_targets())

    with caplog.at_level("INFO"):
        asyncio.run(engine.verify_startup())

    assert calls == Counter({"google": 1, "nvidia": 1, "openrouter": 1, "groq": 1})
    assert "routers: 2/2 available" in caplog.text
    assert "google: 3/3 available" in caplog.text
    assert "nvidia: 4/4 available" in caplog.text
    assert "openrouter: 5/5 available" in caplog.text
    assert "groq: 2/2 available" in caplog.text


def test_provider_specific_resolution_is_narrow_and_preserves_free_variants() -> None:
    assert (
        resolve_catalog_model_id("google", "gemini-3.8-flash", {"models/gemini-3.8-flash"})
        == "models/gemini-3.8-flash"
    )
    for model in (
        "cohere/north-mini-code:free",
        "poolside/laguna-s-2.1:free",
        "google/gemma-4-31b-it:free",
        "google/gemma-4-26b-a4b-it:free",
    ):
        assert resolve_catalog_model_id("openrouter", model, {model}) == model
    assert (
        resolve_catalog_model_id(
            "openrouter", "qwen/qwen3.8-27b:free", {"qwen/qwen3.8-27b"}
        )
        is None
    )
    assert (
        resolve_catalog_model_id("nvidia", "vendor/model-display", {"vendor/model-api"})
        is None
    )


@pytest.mark.parametrize(
    ("payload", "expected_reason"),
    [
        ({"data": []}, "catalog_empty"),
        ({"models": []}, "malformed_catalog"),
        ({"data": [{"name": "model-without-id"}]}, "malformed_catalog"),
        ({"data": [{"id": "different-model"}]}, "model_not_found"),
    ],
)
def test_startup_distinguishes_catalog_failure_conditions(
    monkeypatch, payload: object, expected_reason: str
) -> None:
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, url: str, **_: object) -> httpx.Response:
            return response(200, payload, url)

    monkeypatch.setattr("providers.model_catalog.httpx.AsyncClient", lambda **_: Client())
    verifier = StartupModelVerifier([target("configured", "groq", "expected-model")])
    result = asyncio.run(verifier.verify())[0]

    assert result.available is False
    assert result.reason == expected_reason
    assert result.http_status is None


def test_startup_reports_backend_unreachable_separately(monkeypatch) -> None:
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, url: str, **_: object) -> httpx.Response:
            raise httpx.ConnectError("offline", request=httpx.Request("GET", url))

    monkeypatch.setattr("providers.model_catalog.httpx.AsyncClient", lambda **_: Client())
    verifier = StartupModelVerifier([target("configured", "groq", "expected-model")])
    result = asyncio.run(verifier.verify())[0]

    assert result.available is False
    assert result.reason == "backend_unreachable"


def test_startup_reports_models_endpoint_http_failure(monkeypatch) -> None:
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, url: str, **_: object) -> httpx.Response:
            return response(503, {"error": "withheld"}, url)

    monkeypatch.setattr("providers.model_catalog.httpx.AsyncClient", lambda **_: Client())
    verifier = StartupModelVerifier([target("configured", "groq", "expected-model")])
    result = asyncio.run(verifier.verify())[0]

    assert result.available is False
    assert result.reason == "models_request_failed"
    assert result.http_status == 503


def test_startup_error_identifies_backend_model_and_lookup_reason(monkeypatch) -> None:
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, url: str, **_: object) -> httpx.Response:
            return response(200, {"data": [{"id": "nvidia/current-router"}]}, url)

    monkeypatch.setattr("providers.model_catalog.httpx.AsyncClient", lambda **_: Client())
    engine, _, _ = make_engine(QueueRouter([]), QueueRouter([]))
    engine._startup_model_verifier = StartupModelVerifier(
        [target("fallback_router", "nvidia", "nvidia/obsolete-router", "router")]
    )

    with pytest.raises(RoutingEngineError) as error:
        asyncio.run(engine.verify_startup())

    message = str(error.value)
    assert "fallback_router" in message
    assert "backend=nvidia" in message
    assert "model=nvidia/obsolete-router" in message
    assert "reason=model_not_found" in message
    assert "catalog_match=none" in message

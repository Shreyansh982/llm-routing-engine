from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import create_app
from providers.adapter import ProviderUnavailableError
from tests.conftest import FakeProvider, QueueRouter, decision, make_engine


def test_api_returns_common_success_envelopes_and_provider_safe_data() -> None:
    engine, _, _ = make_engine(QueueRouter([decision("provider_a")]), QueueRouter([]))
    FakeProvider.responses = {"provider_a": "answer"}
    client = TestClient(create_app(engine=engine))

    chat = client.post("/api/v1/chat", json={"conversation_id": "c1", "message": "hello"})
    providers = client.get("/api/v1/providers")
    conversation = client.get("/api/v1/conversation/c1")

    assert chat.status_code == 200 and chat.json()["data"]["action"] == "ANSWER"
    assert providers.json()["data"]["providers"] == [
        {"id": "provider_a", "enabled": True},
        {"id": "provider_b", "enabled": True},
    ]
    assert conversation.json()["data"]["conversation_id"] == "c1"
    assert "diagnostics" not in chat.json()["data"]


def test_developer_header_returns_diagnostics_without_secrets() -> None:
    engine, _, _ = make_engine(QueueRouter([decision("provider_a")]), QueueRouter([]))
    FakeProvider.responses = {"provider_a": "answer"}
    client = TestClient(create_app(engine=engine))

    response = client.post(
        "/api/v1/chat",
        json={"conversation_id": "developer", "message": "hello"},
        headers={"X-Developer-Mode": "true"},
    )

    diagnostics = response.json()["data"]["diagnostics"]
    assert diagnostics["router_used"] == "primary_router"
    assert diagnostics["selected_provider"] == "provider_a"
    assert diagnostics["backend"] == "openrouter"
    assert diagnostics["model"] == "provider_a-model"
    assert diagnostics["provider_backend"] == "openrouter"
    assert diagnostics["configured_model"] == "provider_a-model"
    assert diagnostics["failure_stage"] == "none"
    assert diagnostics["failure_reason"] == "NONE"
    assert diagnostics["request_timestamp"] == diagnostics["timestamp"]
    assert diagnostics["capabilities_considered"]
    assert diagnostics["latency_breakdown"]["total_ms"] >= 0
    assert "api_key" not in str(diagnostics).lower()


def test_developer_configuration_metadata_is_header_gated_and_secret_safe() -> None:
    engine, _, _ = make_engine(QueueRouter([]), QueueRouter([]))
    client = TestClient(create_app(engine=engine))

    normal = client.get("/api/v1/providers")
    developer = client.get("/api/v1/providers", headers={"X-Developer-Mode": "true"})

    assert "diagnostics" not in normal.json()["data"]
    diagnostics = developer.json()["data"]["diagnostics"]
    assert diagnostics["configured_providers"]
    assert "api_key" not in str(diagnostics).lower()


def test_provider_failure_returns_partial_developer_diagnostics_and_classification() -> None:
    engine, _, _ = make_engine(QueueRouter([decision("provider_a")]), QueueRouter([]))
    FakeProvider.fail = True
    client = TestClient(create_app(engine=engine))

    response = client.post(
        "/api/v1/chat",
        json={"conversation_id": "provider-failure", "message": "hello"},
        headers={"X-Developer-Mode": "true"},
    )

    body = response.json()
    assert response.status_code == 503
    assert body["error"]["failure_level"] == "provider"
    assert body["diagnostics"]["developer_mode"] is True
    assert body["diagnostics"]["failure_level"] == "provider"
    assert body["diagnostics"]["selected_provider"] == "provider_a"
    assert body["diagnostics"]["backend"] == "openrouter"
    assert body["diagnostics"]["provider_error"]
    assert body["diagnostics"]["http_status"] == 503
    assert body["diagnostics"]["failure_stage"] == "provider"
    assert body["diagnostics"]["failure_reason"] == "UNKNOWN"


def test_normal_provider_failure_preserves_error_contract_without_diagnostics() -> None:
    engine, _, _ = make_engine(QueueRouter([decision("provider_a")]), QueueRouter([]))
    FakeProvider.fail = True
    client = TestClient(create_app(engine=engine))

    response = client.post("/api/v1/chat", json={"conversation_id": "normal-failure", "message": "hello"})

    body = response.json()
    assert response.status_code == 503
    assert "diagnostics" not in body
    assert "failure_level" not in body["error"]


def test_developer_openrouter_http_429_provider_failure_retains_diagnostics() -> None:
    engine, _, _ = make_engine(QueueRouter([decision("provider_a")]), QueueRouter([]))
    FakeProvider.failure = ProviderUnavailableError(
        "rate limited", http_status=429, failure_reason="HTTP_429_RATE_LIMIT"
    )
    client = TestClient(create_app(engine=engine))

    response = client.post(
        "/api/v1/chat",
        json={"conversation_id": "rate-limit-failure", "message": "hello"},
        headers={"X-Developer-Mode": "true"},
    )

    diagnostics = response.json()["diagnostics"]
    assert response.status_code == 503  # Stable public provider-unavailable contract.
    assert diagnostics["upstream_http_status"] == 429
    assert diagnostics["http_status"] == 429
    assert diagnostics["failure_stage"] == "provider"
    assert diagnostics["failure_reason"] == "HTTP_429_RATE_LIMIT"
    assert diagnostics["provider_id"] == "provider_a"
    assert diagnostics["router_used"] == "primary_router"
    assert diagnostics["backend"] == "openrouter"
    assert diagnostics["configured_model"] == "provider_a-model"
    assert diagnostics["retry_count"] == 0
    assert diagnostics["fallback_used"] is False
    assert diagnostics["timestamp"]


def test_api_returns_contract_errors_for_invalid_and_unknown_conversations() -> None:
    engine, _, _ = make_engine(QueueRouter([]), QueueRouter([]))
    client = TestClient(create_app(engine=engine))

    invalid = client.post("/api/v1/chat", json={"conversation_id": "", "message": ""})
    missing = client.get("/api/v1/conversation/missing")

    assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "INVALID_REQUEST"
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "INVALID_CONVERSATION"

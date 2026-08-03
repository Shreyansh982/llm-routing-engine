from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import create_app
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


def test_api_returns_contract_errors_for_invalid_and_unknown_conversations() -> None:
    engine, _, _ = make_engine(QueueRouter([]), QueueRouter([]))
    client = TestClient(create_app(engine=engine))

    invalid = client.post("/api/v1/chat", json={"conversation_id": "", "message": ""})
    missing = client.get("/api/v1/conversation/missing")

    assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "INVALID_REQUEST"
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "INVALID_CONVERSATION"

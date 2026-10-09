from __future__ import annotations

import asyncio
import base64
import csv
import io
import json
import time
from contextlib import nullcontext
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.main import create_app
from config.settings import Settings, get_settings
from dashboard.pages import chat
from dashboard.utils.api_client import RoutingEngineApiClient, extract_diagnostics
from dashboard.utils.records import TECHNICAL_UNAVAILABLE, new_record, to_csv
from gateway.response_gateway import ResponseGateway
from providers.adapter import ProviderUnavailableError, classify_http_failure
from providers.circuit_breaker import BackendCircuits
from providers.dispatcher import ProviderDispatcher
from providers.model_catalog import StartupModelVerifier
from registry.model_registry import ModelRegistry
from routing.engine import DeadlineExceededError, InvalidRoutingRequestError
from schemas.models import ChatRequest, FailureScope, ImageInput, ProviderRequest, ProviderResponse, RouterDecision
from tests.conftest import PNG_BASE64, FakeProvider, QueueRouter, decision, make_engine, provider
from tests.test_router_reliability import router
from tests.test_startup_model_verification import CATALOGS, configured_targets
from validation.decision_validator import DecisionValidator, InvalidDecisionError


@pytest.mark.parametrize("failed", [False, True])
def test_api_client_chat_record_csv_preserves_diagnostics(monkeypatch, failed):
    engine, _, _ = make_engine(QueueRouter([decision()]), QueueRouter([]))
    if failed:
        FakeProvider.failure = ProviderUnavailableError(
            "private upstream body", http_status=429, failure_reason="HTTP_429_RATE_LIMIT",
            failure_scope=FailureScope.MODEL_SPECIFIC,
        )
    api = TestClient(create_app(engine=engine))
    real_payloads = []

    class TransportClient:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def request(self, method, url, **kwargs):
            response = api.request(method, url, **kwargs)
            real_payloads.append(response.json())
            return response

    monkeypatch.setattr("dashboard.utils.api_client.httpx.Client", lambda **_: TransportClient())
    session = SimpleNamespace(active_conversation_id="cross-layer", developer_mode=True,
                              evaluation_history=[], evaluation_dataset=[], evaluation_mode=True)
    monkeypatch.setattr(chat.st, "session_state", session)
    monkeypatch.setattr(chat.st, "spinner", lambda *_: nullcontext())
    monkeypatch.setattr(chat.st, "error", lambda *_: None)
    monkeypatch.setattr(chat, "status_badge", lambda *_: None)
    monkeypatch.setattr(chat, "append_message", lambda *_: None)
    chat._send(RoutingEngineApiClient("http://testserver/api/v1"), "hello", False, None)
    record = session.last_request
    diagnostics = extract_diagnostics(real_payloads[0])
    assert record["diagnostics"] == diagnostics
    assert record["request_id"] == diagnostics["request_id"]
    assert record["retry_count"] == diagnostics["retry_count"] == 0
    assert record["http_status"] == (503 if failed else 200)
    assert record["upstream_status"] == (429 if failed else None)
    assert record["router_fallback_used"] is False
    assert record["provider_fallback_used"] is failed
    if failed:
        assert record["selected_provider"] is None
        assert diagnostics["fallback_path"][0]["failure_class"] == "HTTP_429"
    row = next(csv.DictReader(io.StringIO(to_csv([record]))))
    for key in ("diagnostics", "fallback_path", "router_attempts", "circuit_state"):
        assert json.loads(row[key]) == record[key]
    assert "private upstream body" not in to_csv([record])


def test_missing_and_null_diagnostics_are_distinct():
    args = dict(timestamp="now", conversation_id="c", prompt="p", response="r", status="success",
                action="ANSWER", latency_ms=1, retry_count=99, http_status=200, error=None)
    absent = new_record(**args)
    present = new_record(**args, diagnostics={"request_id": None, "retry_count": None, "model": None})
    assert absent["request_id"] is None and present["request_id"] is None
    assert absent["retry_count"] == TECHNICAL_UNAVAILABLE
    assert present["retry_count"] is None and present["model"] is None
    assert extract_diagnostics({"data": {}, "diagnostics": {"request_id": "actual"}}) == {"request_id": "actual"}


@pytest.mark.parametrize("text", [
    "BERT was released by Google. GPT-4 is a model. Gemini is another model. North-Eastern.",
    "```python\nname = 'I am Gemini'\ndef f():\n    return 'GPT-4'\n```\n",
    "~~~python\nI am Gemini\n~~~~\n",
    "    def f():\n        return 'Gemini'\n\tprint('Google')\n",
    "| model  | vendor |\n| ------ | ------ |\n| GPT-4  | OpenAI |\n\nmodel: gpt-4\n",
    "Examples include `I am Gemini` and the quote \"I am GPT-4\".\n",
    "As Google, Meta and OpenAI compete...",
    "I am North of the river",
    "I am Google's biggest fan",
    "I am Google’s biggest fan",
    "As Google, Meta and OpenAI compete, I expect better models.",
    "BERT was trained by Google. DeepSeek-V3 is a model.",
    "Models trained by Google include BERT.",
    "```text\nHello, I'm Gemini\nI am DeepSeek-V3\nI was trained by Google\n```\n",
    "    I was trained by Google\n",
])
def test_gateway_preserves_legitimate_text_exactly(text):
    gateway = ResponseGateway(["Gemini", "Google", "GPT-4", "North", "OpenAI", "DeepSeek"])
    assert gateway.process(ProviderResponse(response=text)).response == text


@pytest.mark.parametrize("text", ["I am Gemini.", "I am GPT-4.", "As Gemini, I can help.",
                                  "My name is Gemini.", "I'm Gemini."])
def test_gateway_scrubs_actual_self_identification(text):
    result = ResponseGateway(["Gemini", "GPT-4"]).process(ProviderResponse(response=text)).response
    assert "Gemini" not in result and "GPT-4" not in result
    assert "AI assistant" in result


@pytest.mark.parametrize("text,expected", [
    ("Hello, I am Gemini.", "Hello, I am an AI assistant."),
    ("  I am Gemini.", "  I am an AI assistant."),
    ("I  am Gemini.", "I am an AI assistant."),
    ("I’m Gemini.", "I am an AI assistant."),
    ("**I am Gemini.**", "**I am an AI assistant.**"),
    ("- I am Gemini.", "- I am an AI assistant."),
    ("As Gemini, I can help.", "As an AI assistant, I can help."),
    ("Hello, I'm Gemini", "Hello, I am an AI assistant"),
    ("**I am Gemini**", "**I am an AI assistant**"),
    ("I am DeepSeek-V3", "I am an AI assistant"),
    ("1. I am Gemini", "1. I am an AI assistant"),
    ("* I am Gemini", "* I am an AI assistant"),
    ("I was trained by Google", "I am an AI assistant"),
    ("I am an AI assistant trained by Google.", "I am an AI assistant."),
    ("I'm a large language model trained by Google.", "I am an AI assistant."),
    ("As an AI trained by Google, I can help.", "As an AI assistant, I can help."),
])
def test_gateway_scrubs_self_introductions_without_changing_surrounding_format(text, expected):
    result = ResponseGateway(["Gemini", "DeepSeek", "Google"]).process(ProviderResponse(response=text))
    assert result.response == expected


@pytest.mark.parametrize("developer_mode", [False, True])
def test_v2_unknown_chat_policy_returns_documented_404(developer_mode):
    primary = QueueRouter([])
    engine, _, _ = make_engine(primary, QueueRouter([]))
    response = TestClient(create_app(engine=engine)).post(
        "/api/v2/chat",
        headers={"X-Tenant-ID": "tenant", "X-Developer-Mode": str(developer_mode).lower()},
        json={"conversation_id": "unknown-policy", "message": "hello",
              "routing_context": {"policy_id": "missing-policy"}},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "POLICY_NOT_FOUND"
    assert primary.calls == [] and FakeProvider.calls == []
    if developer_mode:
        assert response.json()["diagnostics"]["http_status"] == 404


def test_three_turn_conversation_recovers_after_retry_exhaustion():
    retry = RouterDecision(action="RETRY", confidence=1, reason="internal retry")
    primary = QueueRouter([decision(), retry, retry, decision()])
    engine, state, _ = make_engine(primary, QueueRouter([]))
    asyncio.run(engine.handle(ChatRequest(conversation_id="turns", message="first")))
    stopped = asyncio.run(engine.handle(ChatRequest(conversation_id="turns", message="first", retry=True)))
    assert stopped.action == "STOP" and state.load("turns").attempt_count == 3
    third = asyncio.run(engine.handle(ChatRequest(conversation_id="turns", message="unrelated new turn")))
    assert third.action == "ANSWER"
    request = primary.calls[-1]
    assert request.original_query == "unrelated new turn"
    assert request.attempt == 0 and request.retry is False
    assert request.previous_response == "answer: first"
    assert state.load("turns").excluded_providers == []


def test_new_turn_rejects_router_retry_even_with_previous_response():
    retry = RouterDecision(action="RETRY", confidence=1, reason="retry")
    with pytest.raises(InvalidDecisionError):
        DecisionValidator().validate(retry, ["general"], False, retry=False)
    primary, fallback = QueueRouter([decision(), retry]), QueueRouter([decision()])
    engine, state, _ = make_engine(primary, fallback)
    asyncio.run(engine.handle(ChatRequest(conversation_id="c", message="first")))
    result = asyncio.run(engine.handle(ChatRequest(conversation_id="c", message="second")))
    assert result.action == "ANSWER" and len(fallback.calls) == 1
    assert state.load("c").attempt_count == 0


@pytest.mark.parametrize("action", ["CLARIFY", "STOP"])
def test_router_internal_reason_is_not_the_user_response(action):
    internal = "internal routing model unavailable"
    engine, _, _ = make_engine(QueueRouter([RouterDecision(action=action, confidence=1, reason=internal)]), QueueRouter([]))
    result = asyncio.run(engine.handle(ChatRequest(conversation_id="c", message="help"), developer_mode=True))
    assert internal not in result.response
    assert result.diagnostics.routing_reason == internal
    assert FakeProvider.calls == []


def test_global_deadline_cancels_provider_and_skips_remaining_candidates(monkeypatch):
    engine, state, _ = make_engine(QueueRouter([decision()]), QueueRouter([]))
    engine._overall_request_timeout = 0.04
    calls, cancelled = [], []

    async def slow(self, request):
        calls.append(self.config.id)
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(self.config.id)
        return ProviderResponse(response="late")

    monkeypatch.setattr(FakeProvider, "generate", slow)
    started = time.monotonic()
    with pytest.raises(DeadlineExceededError) as error:
        asyncio.run(engine.handle(ChatRequest(conversation_id="slow", message="hello"), developer_mode=True))
    assert time.monotonic() - started < 1
    assert calls == cancelled == ["provider_a"]
    diagnostic = error.value.diagnostics
    assert diagnostic.failure_stage == "deadline"
    assert diagnostic.failure_class == diagnostic.failure_reason == "DEADLINE_EXCEEDED"
    assert diagnostic.fallback_path[-1]["outcome"] == "skipped_deadline"
    assert state.load("slow").last_provider is None
    assert state.load("slow").previous_response is None


def test_provider_timeout_allows_bounded_fallback(monkeypatch):
    first = provider("first", ["general"]).model_copy(update={"timeout": 0.02})
    engine, _, _ = make_engine(QueueRouter([decision()]), QueueRouter([]), [first, provider("second", ["general"], priority=2)])

    async def generate(self, request):
        if self.config.id == "first":
            await asyncio.sleep(10)
        return ProviderResponse(response="done")

    monkeypatch.setattr(FakeProvider, "generate", generate)
    result = asyncio.run(engine.handle(ChatRequest(conversation_id="c", message="hello"), developer_mode=True))
    assert result.response == "done"
    assert result.diagnostics.fallback_path[0]["failure_class"] == "TIMEOUT"
    assert result.diagnostics.final_provider == "second"


def test_router_deadline_returns_standard_api_error(monkeypatch):
    primary = QueueRouter([])
    engine, _, _ = make_engine(primary, QueueRouter([]))
    engine._overall_request_timeout = 0.02

    async def slow(_):
        await asyncio.sleep(10)

    monkeypatch.setattr(primary, "decide", slow)
    response = TestClient(create_app(engine=engine)).post("/api/v1/chat",
        json={"conversation_id": "c", "message": "hello"}, headers={"X-Developer-Mode": "true"})
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "DEADLINE_EXCEEDED"
    diagnostics = response.json()["diagnostics"]
    assert diagnostics["http_status"] == 504
    assert diagnostics["router_attempts"][0]["failure_class"] == "DEADLINE_EXCEEDED"


@pytest.mark.parametrize("method,path,body,status", [
    ("post", "/api/v1/chat", {"message": ""}, 422),
    ("post", "/api/v2/chat", {"conversation_id": "c", "message": "hi"}, 422),
    ("get", "/not-a-route", None, 404),
    ("put", "/api/v1/chat", {}, 405),
])
def test_framework_errors_keep_envelope_and_http_semantics(method, path, body, status):
    engine, _, _ = make_engine(QueueRouter([]), QueueRouter([]))
    response = TestClient(create_app(engine=engine)).request(method, path, json=body,
        headers={"X-Tenant-ID": "invalid tenant!", "X-Developer-Mode": "true"})
    assert response.status_code == status
    assert response.json()["success"] is False and "detail" not in response.json()
    assert response.json()["diagnostics"]["failure_stage"] == "request"
    assert response.json()["diagnostics"]["http_status"] == status
    if status == 405:
        assert "POST" in response.headers["allow"]


def test_unexpected_registry_failure_retains_request_and_policy_context(monkeypatch, caplog):
    primary = QueueRouter([decision()])
    engine, _, registry = make_engine(primary, QueueRouter([]))
    original = registry.get_provider

    def broken(provider_id):
        if primary.calls:
            raise RuntimeError("secret internal exception")
        return original(provider_id)

    monkeypatch.setattr(registry, "get_provider", broken)
    def unexpected_id():
        pytest.fail("The engine must reuse the API request ID")

    monkeypatch.setattr("routing.engine.uuid4", unexpected_id)
    app = create_app(engine=engine)
    response = TestClient(app).post("/api/v2/chat", json={"conversation_id": "c", "message": "hello"},
        headers={"X-Developer-Mode": "true", "X-Tenant-ID": "tenant"})
    assert response.status_code == 500
    diagnostic = response.json()["diagnostics"]
    audit = app.state.policy_audit_repository.list("tenant")[0]
    assert diagnostic["request_id"] == audit.request_id
    assert diagnostic["failure_stage"] == "dispatcher"
    assert diagnostic["router_used"] == "primary_router"
    assert diagnostic["fallback_path"][0]["failure_class"] == "UNKNOWN"
    assert diagnostic["request_id"] in caplog.text
    assert "secret internal exception" not in response.text + caplog.text


@pytest.mark.parametrize("enabled,token,authorized", [
    (False, "operator", False), (True, None, False),
    (True, "wrong", False), (True, "operator", True),
])
def test_developer_access_control(enabled, token, authorized):
    engine, _, _ = make_engine(QueueRouter([decision()]), QueueRouter([]))
    settings = Settings(developer_mode_enabled=enabled, developer_token="operator")
    client = TestClient(create_app(engine=engine, settings=settings))
    headers = {"X-Developer-Mode": "true"}
    if token:
        headers["X-Developer-Token"] = token
    response = client.post("/api/v1/chat", headers=headers, json={"conversation_id": "c", "message": "hello"})
    assert response.status_code == 200
    assert ("diagnostics" in response.json()["data"]) is authorized
    for path in ("/api/v1/providers/health", "/api/v1/router/health"):
        assert ("backends" in client.get(path, headers=headers).json()["data"]) is authorized
    failure = client.post("/api/v1/chat", headers=headers, json={})
    assert ("diagnostics" in failure.json()) is authorized


@pytest.mark.parametrize("backend", ["google", "nvidia", "openrouter", "groq"])
def test_model_quota_does_not_open_other_models_backend(backend):
    engine, _, _ = make_engine(QueueRouter([decision()]), QueueRouter([]), [
        provider("first", ["general"], backend=backend),
        provider("second", ["general"], backend=backend, priority=2),
    ])
    engine._dispatcher._circuits = BackendCircuits(failure_threshold=1)
    FakeProvider.failures = {"first": ProviderUnavailableError("quota", http_status=429,
        failure_scope=classify_http_failure(429, "model quota exceeded"))}
    result = asyncio.run(engine.handle(ChatRequest(conversation_id="c", message="hello")))
    assert result.action == "ANSWER"
    assert [call[0] for call in FakeProvider.calls] == ["first", "second"]
    assert engine._dispatcher.circuit_snapshot()[backend]["state"] == "CLOSED"


@pytest.mark.parametrize("component", ["provider", "router"])
@pytest.mark.parametrize("failure", ["cancel", "exception", "timeout"])
def test_half_open_probe_resolved_on_all_exit_paths(monkeypatch, component, failure):
    clock = [0]
    circuits = BackendCircuits(failure_threshold=1, open_seconds=1, clock=lambda: clock[0])
    circuits.record_failure("google", True)
    clock[0] = 2

    async def fail(*_):
        if failure == "exception":
            raise RuntimeError("unexpected")
        if failure == "cancel":
            raise asyncio.CancelledError
        await asyncio.sleep(10)

    if component == "provider":
        config = provider("first", ["general"], backend="google").model_copy(update={"timeout": 0.02})
        dispatcher = ProviderDispatcher(ModelRegistry([config]), circuits, adapter_factory=FakeProvider)
        monkeypatch.setattr(FakeProvider, "generate", fail)
        coroutine = dispatcher.dispatch("first", ProviderRequest(text="hello"))
    else:
        instance = router("google", 0.02, circuits)
        monkeypatch.setattr(instance, "_decide_with_repair", fail)
        coroutine = instance.decide(None)
    with pytest.raises((asyncio.CancelledError, RuntimeError)):
        asyncio.run(coroutine)
    snapshot = circuits.snapshot()["google"]
    assert snapshot["half_open_inflight"] == 0
    if failure == "timeout":
        assert snapshot["state"] == "OPEN"
    else:
        assert circuits.allow_request("google")


def test_images_reject_oversize_before_decoding_and_decode_valid_input_once(monkeypatch):
    original = base64.b64decode
    calls = []

    def decode(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(base64, "b64decode", decode)
    monkeypatch.setattr(get_settings(), "max_image_bytes", 2)
    with pytest.raises(ValidationError, match="size limit"):
        ImageInput(media_type="image/png", data_base64=PNG_BASE64)
    assert not calls
    monkeypatch.setattr(get_settings(), "max_image_bytes", 1024)
    request = ChatRequest(conversation_id="c", message="hi", image={"media_type": "image/png", "data_base64": PNG_BASE64})
    ProviderRequest(text="hi", image=request.image)
    assert calls == [1]


@pytest.mark.parametrize("media_type,encoded", [
    ("image/png", "not base64"), ("image/png", "aW1hZ2U="),
    ("image/jpeg", PNG_BASE64), ("image/webp", PNG_BASE64),
])
def test_malformed_or_mislabeled_images_rejected(media_type, encoded):
    with pytest.raises(ValidationError):
        ImageInput(media_type=media_type, data_base64=encoded)


def test_image_retry_requires_reattachment_and_never_persists_payload():
    engine, state, _ = make_engine(QueueRouter([decision("multimodal"), decision("multimodal")]), QueueRouter([]), [
        provider("first", ["vision"], image=True, task_group="multimodal"),
        provider("second", ["vision"], image=True, task_group="multimodal", priority=2),
    ])
    request = ChatRequest(conversation_id="image", message="describe",
                          image={"media_type": "image/png", "data_base64": PNG_BASE64})
    asyncio.run(engine.handle(request))
    with pytest.raises(InvalidRoutingRequestError):
        asyncio.run(engine.handle(ChatRequest(conversation_id="image", message="describe", retry=True)))
    assert state.load("image").attempt_count == 0
    result = asyncio.run(engine.handle(request.model_copy(update={"retry": True}), developer_mode=True))
    assert result.action == "ANSWER"
    assert FakeProvider.calls[-1][1].image == request.image
    assert PNG_BASE64 not in state.load("image").model_dump_json() + result.diagnostics.model_dump_json()


def test_image_added_during_retry_also_requires_reattachment():
    engine, _, _ = make_engine(QueueRouter([decision(), decision("multimodal")]), QueueRouter([]), [
        provider("text", ["general"]),
        provider("vision", ["vision"], image=True, task_group="multimodal"),
    ])
    asyncio.run(engine.handle(ChatRequest(conversation_id="c", message="describe")))
    asyncio.run(engine.handle(ChatRequest(conversation_id="c", message="describe", retry=True,
        image={"media_type": "image/png", "data_base64": PNG_BASE64})))
    with pytest.raises(InvalidRoutingRequestError):
        asyncio.run(engine.handle(ChatRequest(conversation_id="c", message="describe", retry=True)))


@pytest.mark.parametrize("role,backends", [("provider", 4), ("router", 2)])
def test_health_uses_one_catalog_fetch_per_backend(monkeypatch, role, backends):
    calls = []

    async def catalog(target):
        calls.append(target.backend)
        return frozenset(CATALOGS[target.backend])

    monkeypatch.setattr("providers.model_catalog.load_model_catalog", catalog)
    engine, _, _ = make_engine(QueueRouter([]), QueueRouter([]))
    engine._startup_model_verifier = StartupModelVerifier(configured_targets())
    result = asyncio.run(engine.provider_health() if role == "provider" else engine.router_health())
    assert len(calls) == len(set(calls)) == backends
    assert result


def test_unexpected_failure_outside_engine_is_sanitized_and_logged(monkeypatch, caplog):
    engine, _, _ = make_engine(QueueRouter([]), QueueRouter([]))

    def broken():
        raise RuntimeError("sensitive framework details")

    monkeypatch.setattr(engine, "provider_summaries", broken)
    response = TestClient(create_app(engine=engine)).get("/api/v1/providers", headers={"X-Developer-Mode": "true"})
    assert response.status_code == 500
    diagnostic = response.json()["diagnostics"]
    assert diagnostic["request_id"] in caplog.text
    assert "sensitive framework details" not in response.text + caplog.text


def test_dispatcher_health_deduplicates_catalog_requests(monkeypatch):
    calls = []
    configs = [provider("first", ["general"]), provider("second", ["general"], priority=2),
               provider("disabled", ["general"], enabled=False, priority=3)]
    configs = [config.model_copy(update={"endpoint": "http://backend.invalid/chat/completions"}) for config in configs]

    async def catalog(target):
        calls.append(target.backend)
        return frozenset(config.model for config in configs)

    monkeypatch.setattr("providers.model_catalog.load_model_catalog", catalog)
    dispatcher = ProviderDispatcher(ModelRegistry(configs), BackendCircuits())
    assert asyncio.run(dispatcher.health()) == {"first": "healthy", "second": "healthy", "disabled": "offline"}
    assert calls == ["openrouter"]


def test_dashboard_starts_without_secrets_and_analytics_handles_missing_diagnostics(monkeypatch):
    from streamlit.errors import StreamlitSecretNotFoundError
    from streamlit.testing.v1 import AppTest

    class NoSecrets:
        def get(self, *_):
            raise StreamlitSecretNotFoundError("No secrets configured")

    monkeypatch.setattr("streamlit.secrets", NoSecrets())
    app = AppTest.from_file("dashboard/app.py").run()
    assert not app.exception
    app.session_state.evaluation_history = [new_record(
        timestamp="now", conversation_id="c", prompt="hi", response="hello",
        status="success", action="ANSWER", latency_ms=1, retry_count=None,
        http_status=200, error=None,
    )]
    app.sidebar.radio[0].set_value("Analytics").run()
    assert not app.exception
    assert any(metric.label == "Retry rate" and metric.value == TECHNICAL_UNAVAILABLE for metric in app.metric)


def test_dashboard_client_sends_token_only_for_developer_requests(monkeypatch):
    headers = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def request(self, method, url, **kwargs):
            headers.append(kwargs["headers"])
            return httpx.Response(200, json={"success": True, "data": {}}, request=httpx.Request(method, url))

    monkeypatch.setattr("dashboard.utils.api_client.httpx.Client", lambda **_: Client())
    client = RoutingEngineApiClient("http://api.invalid/api/v1", developer_token="operator")
    client.chat("c", "hello")
    client.chat("c", "hello", developer_mode=True)
    assert "X-Developer-Token" not in headers[0]
    assert headers[1] == {"X-Developer-Mode": "true", "X-Developer-Token": "operator"}

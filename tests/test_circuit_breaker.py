from __future__ import annotations

import asyncio

import httpx
import pytest

from config.settings import RouterBackendConfig
from providers.adapter import ProviderUnavailableError, classify_http_failure
from providers.circuit_breaker import BackendCircuit, BackendCircuits, CircuitState
from providers.dispatcher import ProviderDispatcher
from registry.model_registry import ModelRegistry
from routers.http_router import RouterUnavailableError, StructuredHTTPRouter
from schemas.models import FailureScope, ProviderRequest, RouterRequest
from tests.conftest import FakeProvider, provider


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_circuit_state_machine_uses_bounded_half_open_probe_without_sleep() -> None:
    clock = FakeClock()
    circuit = BackendCircuit(3, 30, 1, clock=clock)
    circuit.record_failure(True)
    circuit.record_failure(True)
    assert circuit.state == CircuitState.CLOSED
    circuit.record_success()
    assert circuit.consecutive_failures == 0

    for _ in range(3):
        circuit.record_failure(True)
    assert circuit.state == CircuitState.OPEN
    assert circuit.allow_request() is False

    clock.now = 30
    before = circuit.state
    assert circuit.snapshot()["state"] == "HALF_OPEN"
    assert circuit.state == before  # Health snapshots never mutate circuit state.
    assert circuit.allow_request() is True
    assert circuit.state == CircuitState.HALF_OPEN
    assert circuit.allow_request() is False
    circuit.record_success()
    assert circuit.state == CircuitState.CLOSED


def test_failed_half_open_probe_reopens_circuit() -> None:
    clock = FakeClock()
    circuit = BackendCircuit(1, 30, 1, clock=clock)
    circuit.record_failure(True)
    clock.now = 31
    assert circuit.allow_request() is True
    circuit.record_failure(True)
    assert circuit.state == CircuitState.OPEN
    assert circuit.opened_at == 31


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_backend_http_failures_are_transient(status: int) -> None:
    assert classify_http_failure(status, "temporary backend failure") == FailureScope.BACKEND_TRANSIENT


@pytest.mark.parametrize("status", [400, 401, 403])
def test_configuration_http_failures_do_not_count(status: int) -> None:
    assert classify_http_failure(status, "invalid request") == FailureScope.NON_TRANSIENT


def test_model_specific_failure_does_not_disable_backend() -> None:
    assert classify_http_failure(500, "model unavailable") == FailureScope.MODEL_SPECIFIC
    assert classify_http_failure(404, "not found") == FailureScope.MODEL_SPECIFIC


def test_shared_backend_open_skips_every_model() -> None:
    registry = ModelRegistry(
        [
            provider("g3", ["general"], backend="groq", priority=1),
            provider("c1", ["coding"], backend="groq", task_group="coding_reasoning", priority=1),
        ]
    )
    circuits = BackendCircuits(failure_threshold=1)
    circuits.record_failure("groq", True)
    FakeProvider.calls = []
    dispatcher = ProviderDispatcher(registry, circuits, adapter_factory=FakeProvider)

    for provider_id in ("g3", "c1"):
        with pytest.raises(ProviderUnavailableError, match="circuit is open"):
            asyncio.run(dispatcher.dispatch(provider_id, ProviderRequest(text="hello")))
    assert FakeProvider.calls == []


def test_google_open_circuit_is_shared_by_router_and_task_model(monkeypatch) -> None:
    circuits = BackendCircuits(failure_threshold=1)
    circuits.record_failure("google", True)
    config = RouterBackendConfig(
        backend="google",
        endpoint="http://google.invalid/chat/completions",
        model="gemini-3.8-flash",
        api_key="test-key",
        timeout=1,
    )
    router = StructuredHTTPRouter(config, circuits)
    request = RouterRequest(
        original_query="hello",
        latest_user_message="hello",
        attempt=0,
        max_attempts=3,
        available_task_groups=["general"],
    )
    with pytest.raises(RouterUnavailableError, match="circuit is open"):
        asyncio.run(router.decide(request))

    registry = ModelRegistry([provider("google_task", ["general"], backend="google")])
    dispatcher = ProviderDispatcher(registry, circuits, adapter_factory=FakeProvider)
    with pytest.raises(ProviderUnavailableError, match="circuit is open"):
        asyncio.run(dispatcher.dispatch("google_task", ProviderRequest(text="hello")))


def test_timeout_and_connection_failures_are_backend_transient() -> None:
    timeout = httpx.ReadTimeout("timeout")
    network = httpx.ConnectError("connection")
    from providers.adapter import OpenAICompatibleAdapter

    assert OpenAICompatibleAdapter._failure_metadata(timeout)[2] == FailureScope.BACKEND_TRANSIENT
    assert OpenAICompatibleAdapter._failure_metadata(network)[2] == FailureScope.BACKEND_TRANSIENT

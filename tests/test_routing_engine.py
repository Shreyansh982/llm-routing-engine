from __future__ import annotations

import asyncio

import pytest

from providers.adapter import ProviderUnavailableError
from routing.engine import ProviderExecutionError, RoutingEngineError
from schemas.models import ChatRequest, RouterAction, RouterDecision, TaskGroup
from tests.conftest import FakeProvider, QueueRouter, decision, make_engine, provider


def test_normal_request_dispatches_provider_and_gateway_processes_every_response() -> None:
    primary = QueueRouter([decision()])
    engine, state, _ = make_engine(primary, QueueRouter([]))
    FakeProvider.responses = {"provider_a": "I am Provider A. A useful answer."}

    result = asyncio.run(engine.handle(ChatRequest(conversation_id="c1", message="write code")))

    assert result.action == RouterAction.ANSWER
    assert result.response == "I am an AI assistant. A useful answer."
    assert state.load("c1").last_provider == "provider_a"
    assert primary.calls[0].available_task_groups == [TaskGroup.GENERAL]


def test_explicit_retry_excludes_previous_provider_and_reroutes_deterministically() -> None:
    primary = QueueRouter([decision(), decision()])
    engine, state, _ = make_engine(primary, QueueRouter([]))
    FakeProvider.responses = {"provider_a": "first", "provider_b": "second"}
    asyncio.run(engine.handle(ChatRequest(conversation_id="c1", message="initial")))

    result = asyncio.run(engine.handle(ChatRequest(conversation_id="c1", message="retry this", retry=True)))

    loaded = state.load("c1")
    assert result.response == "second"
    assert loaded.attempt_count == 1 and loaded.excluded_providers == ["provider_a"]
    assert primary.calls[1].available_task_groups == [TaskGroup.GENERAL]


def test_primary_invalid_decision_uses_fallback_router() -> None:
    bad = RouterDecision(action="ANSWER", task_group="multimodal", confidence=0.9, reason="bad")
    primary, fallback = QueueRouter([bad]), QueueRouter([decision()])
    engine, _, _ = make_engine(primary, fallback)
    FakeProvider.responses = {"provider_a": "fallback answer"}

    result = asyncio.run(engine.handle(ChatRequest(conversation_id="c1", message="hello")))
    assert result.response == "fallback answer" and len(fallback.calls) == 1


def test_developer_diagnostics_identify_the_fallback_router() -> None:
    primary = QueueRouter([RuntimeError("timeout")])
    fallback = QueueRouter([decision()])
    engine, _, _ = make_engine(primary, fallback)
    FakeProvider.responses = {"provider_a": "fallback answer"}

    result = asyncio.run(engine.handle(ChatRequest(conversation_id="c1", message="hello"), developer_mode=True))

    assert result.diagnostics is not None
    assert result.diagnostics.router_used == "fallback_router"
    assert result.diagnostics.fallback_used is True
    assert result.diagnostics.selected_provider == "provider_a"
    assert result.diagnostics.failure_stage == "none"
    assert result.diagnostics.failure_reason == "NONE"
    assert result.diagnostics.latency_breakdown.provider_ms >= 0


def test_complete_router_failure_is_controlled() -> None:
    primary = QueueRouter([RuntimeError("timeout")])
    fallback = QueueRouter([RuntimeError("invalid")])
    engine, _, _ = make_engine(primary, fallback)
    with pytest.raises(RoutingEngineError):
        asyncio.run(engine.handle(ChatRequest(conversation_id="c1", message="hello")))
    assert len(primary.calls) == 1 and len(fallback.calls) == 1


def test_retry_limit_and_pool_exhaustion_stop_without_another_router_call() -> None:
    primary = QueueRouter([decision()])
    engine, _, _ = make_engine(primary, QueueRouter([]), [provider("provider_a", ["coding"])], max_attempts=1)
    asyncio.run(engine.handle(ChatRequest(conversation_id="c1", message="hello")))

    stopped = asyncio.run(engine.handle(ChatRequest(conversation_id="c1", message="again", retry=True)))

    assert stopped.action == RouterAction.STOP
    assert len(primary.calls) == 1


def test_router_retry_action_is_bounded_and_clarify_skips_provider_dispatch() -> None:
    retry = RouterDecision(action="RETRY", confidence=0.5, reason="retry")
    clarify = RouterDecision(action="CLARIFY", confidence=0.7, reason="Which language?")
    primary = QueueRouter([retry, clarify])
    engine, state, _ = make_engine(primary, QueueRouter([]))
    result = asyncio.run(engine.handle(ChatRequest(conversation_id="c1", message="initial")))

    assert result.action == RouterAction.CLARIFY and result.response == "Which language?"
    assert state.load("c1").attempt_count == 1


def test_provider_failure_becomes_controlled_engine_error() -> None:
    engine, _, _ = make_engine(QueueRouter([decision()]), QueueRouter([]))
    FakeProvider.fail = True
    with pytest.raises(ProviderExecutionError):
        asyncio.run(engine.handle(ChatRequest(conversation_id="c1", message="hello")))

from __future__ import annotations

import json
import os
from collections.abc import Sequence

# Importing api.main creates the ASGI app. Keep that import independent from a developer's
# private .env by supplying a complete, secret-free test configuration first.
os.environ.update(
    {
        "GOOGLE_API_KEY": "test-key",
        "NVIDIA_API_KEY": "test-key",
        "OPENROUTER_API_KEY": "test-key",
        "GROQ_API_KEY": "test-key",
        "GOOGLE_ENDPOINT": "http://google.invalid/chat/completions",
        "NVIDIA_ENDPOINT": "http://nvidia.invalid/chat/completions",
        "OPENROUTER_ENDPOINT": "http://openrouter.invalid/chat/completions",
        "GROQ_ENDPOINT": "http://groq.invalid/chat/completions",
        "PRIMARY_ROUTER_BACKEND": "google",
        "PRIMARY_ROUTER_MODEL": "gemini-3.8-flash",
        "FALLBACK_ROUTER_BACKEND": "nvidia",
        "FALLBACK_ROUTER_MODEL": "z-ai/glm-5-3-flash",
        "PROVIDERS_JSON": json.dumps(
            [
                {
                    "id": f"test_{group}",
                    "backend": backend,
                    "model": f"test-{group}",
                    "task_group": group,
                    "priority": 1,
                    "capabilities": {
                        "strengths": [group],
                        "speed_tier": "test",
                        "context_size": "test",
                        "input_modalities": ["text", "image"] if group == "multimodal" else ["text"],
                    },
                }
                for group, backend in (
                    ("general", "google"),
                    ("coding_reasoning", "groq"),
                    ("creative", "openrouter"),
                    ("multimodal", "nvidia"),
                )
            ]
        ),
    }
)

from core.interfaces import BaseProvider, BaseRouter
from gateway.response_gateway import ResponseGateway
from providers.circuit_breaker import BackendCircuits
from providers.dispatcher import ProviderDispatcher
from registry.model_registry import ModelRegistry
from routing.engine import RoutingEngine
from schemas.models import (
    ProviderCapability,
    ProviderConfig,
    ProviderRequest,
    ProviderResponse,
    RouterDecision,
    RouterRequest,
    TaskGroup,
)
from state.manager import ConversationStateManager
from validation.decision_validator import DecisionValidator


def provider(
    provider_id: str,
    strengths: list[str],
    *,
    enabled: bool = True,
    backend: str = "openrouter",
    task_group: TaskGroup = TaskGroup.GENERAL,
    priority: int = 1,
    image: bool = False,
) -> ProviderConfig:
    return ProviderConfig(
        id=provider_id,
        enabled=enabled,
        backend=backend,
        endpoint=f"http://{provider_id}.invalid/chat/completions",
        model=f"{provider_id}-model",
        task_group=task_group,
        priority=priority,
        api_key="test-key",
        timeout=1,
        capabilities=ProviderCapability(
            strengths=strengths,
            speed_tier="standard",
            context_size="large",
            input_modalities=["text", "image"] if image else ["text"],
        ),
        identity_terms=[provider_id.replace("_", " ")],
    )


class QueueRouter(BaseRouter):
    def __init__(self, responses: Sequence[RouterDecision | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[RouterRequest] = []

    async def decide(self, request: RouterRequest) -> RouterDecision:
        self.calls.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def health(self) -> str:
        return "healthy"


class FakeProvider(BaseProvider):
    responses: dict[str, str] = {}
    fail = False
    failure: Exception | None = None
    failures: dict[str, Exception] = {}
    calls: list[tuple[str, ProviderRequest]] = []

    def __init__(self, config: ProviderConfig, _: float) -> None:
        self.config = config

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append((self.config.id, request))
        if self.config.id in self.failures:
            raise self.failures[self.config.id]
        if self.failure is not None:
            raise self.failure
        if self.fail:
            from providers.adapter import ProviderUnavailableError

            raise ProviderUnavailableError("down")
        return ProviderResponse(response=self.responses.get(self.config.id, f"answer: {request.text}"))

    async def health(self) -> str:
        return "healthy"


def decision(task_group: TaskGroup = TaskGroup.GENERAL) -> RouterDecision:
    return RouterDecision(action="ANSWER", task_group=task_group, confidence=0.9, reason="task match")


def make_engine(
    primary: BaseRouter,
    fallback: BaseRouter,
    providers: list[ProviderConfig] | None = None,
    max_attempts: int = 3,
) -> tuple[RoutingEngine, ConversationStateManager, ModelRegistry]:
    registry = ModelRegistry(providers or [provider("provider_a", ["coding"]), provider("provider_b", ["creative_writing"])])
    state = ConversationStateManager()
    FakeProvider.responses = {}
    FakeProvider.fail = False
    FakeProvider.failure = None
    FakeProvider.failures = {}
    FakeProvider.calls = []
    circuits = BackendCircuits()
    engine = RoutingEngine(
        state_manager=state,
        registry=registry,
        primary_router=primary,
        fallback_router=fallback,
        validator=DecisionValidator(),
        dispatcher=ProviderDispatcher(registry, circuits, adapter_factory=FakeProvider),
        gateway=ResponseGateway(["provider a", "provider b", "Claude"]),
        max_attempts=max_attempts,
    )
    return engine, state, registry

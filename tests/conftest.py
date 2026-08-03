from __future__ import annotations

from collections.abc import Sequence

from core.interfaces import BaseProvider, BaseRouter
from gateway.response_gateway import ResponseGateway
from providers.dispatcher import ProviderDispatcher
from registry.model_registry import ModelRegistry
from routers.default_router import DeterministicDefaultRouter
from routing.engine import RoutingEngine
from schemas.models import ProviderCapability, ProviderConfig, ProviderResponse, RouterDecision, RouterRequest
from state.manager import ConversationStateManager
from validation.decision_validator import DecisionValidator


def provider(
    provider_id: str,
    strengths: list[str],
    *,
    enabled: bool = True,
) -> ProviderConfig:
    return ProviderConfig(
        id=provider_id,
        enabled=enabled,
        endpoint=f"http://{provider_id}.invalid/chat",
        model=f"{provider_id}-model",
        capabilities=ProviderCapability(strengths=strengths, speed_tier="standard", context_size="large"),
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

    def __init__(self, config: ProviderConfig, _: float) -> None:
        self.config = config

    async def generate(self, prompt: str) -> ProviderResponse:
        if self.fail:
            from providers.adapter import ProviderUnavailableError

            raise ProviderUnavailableError("down")
        return ProviderResponse(response=self.responses.get(self.config.id, f"answer: {prompt}"))

    async def health(self) -> str:
        return "healthy"


def decision(provider_id: str) -> RouterDecision:
    return RouterDecision(action="ANSWER", selected_provider=provider_id, confidence=0.9, reason="capability match")


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
    engine = RoutingEngine(
        state_manager=state,
        registry=registry,
        primary_router=primary,
        fallback_router=fallback,
        default_router=DeterministicDefaultRouter(registry),
        validator=DecisionValidator(registry),
        dispatcher=ProviderDispatcher(registry, 1, adapter_factory=FakeProvider),
        gateway=ResponseGateway(["provider a", "provider b", "Claude"]),
        max_attempts=max_attempts,
    )
    return engine, state, registry

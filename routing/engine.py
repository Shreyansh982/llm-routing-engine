"""The deterministic request-lifecycle coordinator."""

from __future__ import annotations

import logging
import time

from core.interfaces import BaseRouter
from gateway.response_gateway import ResponseGateway
from providers.adapter import ProviderUnavailableError
from providers.dispatcher import ProviderDisabledError, ProviderDispatcher
from registry.model_registry import ModelRegistry
from routers.default_router import DeterministicDefaultRouter
from schemas.models import ChatRequest, ConversationState, RouterAction, RouterDecision, RouterRequest, RoutingResponse
from state.manager import ConversationNotFoundError, ConversationStateManager
from validation.decision_validator import DecisionValidator, InvalidDecisionError

logger = logging.getLogger(__name__)


class RoutingEngineError(RuntimeError):
    code = "ROUTER_FAILURE"


class ProviderExecutionError(RoutingEngineError):
    code = "PROVIDER_UNAVAILABLE"


class RoutingEngine:
    """Coordinates deterministic infrastructure around the single active AI router."""

    STOP_MESSAGE = "Unable to produce a better response."

    def __init__(
        self,
        state_manager: ConversationStateManager,
        registry: ModelRegistry,
        primary_router: BaseRouter,
        fallback_router: BaseRouter,
        default_router: DeterministicDefaultRouter,
        validator: DecisionValidator,
        dispatcher: ProviderDispatcher,
        gateway: ResponseGateway,
        max_attempts: int,
    ) -> None:
        self._state = state_manager
        self._registry = registry
        self._primary = primary_router
        self._fallback = fallback_router
        self._default = default_router
        self._validator = validator
        self._dispatcher = dispatcher
        self._gateway = gateway
        self._max_attempts = max_attempts

    async def handle(self, request: ChatRequest) -> RoutingResponse:
        started = time.perf_counter()
        if request.retry:
            state = self._state.load(request.conversation_id)
            stopped = self._prepare_retry(state)
            if stopped:
                return self._stop_response(state.conversation_id)
        else:
            try:
                state = self._state.load(request.conversation_id)
            except ConversationNotFoundError:
                state = self._state.create(request.conversation_id, request.message, self._max_attempts)

        result = await self._route(state, request.message)
        logger.info(
            "routing_complete conversation_id=%s action=%s attempt_count=%s latency_ms=%.2f",
            state.conversation_id,
            result.action,
            state.attempt_count,
            (time.perf_counter() - started) * 1000,
        )
        return result

    def conversation_state(self, conversation_id: str) -> ConversationState:
        """Expose the documented POC debugging view without leaking state ownership."""
        return self._state.load(conversation_id)

    def provider_summaries(self) -> list[dict[str, object]]:
        """Return only provider IDs and enabled state for the public API."""
        return [
            {"id": provider.id, "enabled": provider.enabled}
            for provider in self._registry.list_providers()
        ]

    async def router_health(self) -> dict[str, str]:
        return {
            "primary_router": await self._primary.health(),
            "fallback_router": await self._fallback.health(),
        }

    async def provider_health(self) -> dict[str, str]:
        return await self._dispatcher.health()

    def _prepare_retry(self, state: ConversationState) -> bool:
        if self._retry_guard_fired(state):
            return True
        if state.last_provider:
            self._state.exclude_provider(state, state.last_provider)
        # An initial RETRY action has no prior provider to exclude. It remains bounded
        # by the same counter; this is the most consistent interpretation of the spec.
        self._state.increment_attempt(state)
        return self._retry_guard_fired(state)

    def _retry_guard_fired(self, state: ConversationState) -> bool:
        return state.attempt_count >= state.max_attempts or not self._registry.available_for_router(
            state.excluded_providers
        )

    async def _route(self, state: ConversationState, latest_message: str) -> RoutingResponse:
        while True:
            if self._retry_guard_fired(state):
                return self._stop_response(state.conversation_id)
            router_request = RouterRequest(
                original_query=state.original_query,
                latest_user_message=latest_message,
                previous_response=state.previous_response,
                attempt=state.attempt_count,
                max_attempts=state.max_attempts,
                excluded_providers=state.excluded_providers,
                available_providers=self._registry.available_for_router(state.excluded_providers),
            )
            decision = await self._decision_from_ladder(router_request, state.excluded_providers)
            if decision.action == RouterAction.RETRY:
                if self._prepare_retry(state):
                    return self._stop_response(state.conversation_id)
                continue
            if decision.action == RouterAction.CLARIFY:
                return RoutingResponse(
                    conversation_id=state.conversation_id,
                    action=RouterAction.CLARIFY,
                    response=decision.reason,
                )
            if decision.action == RouterAction.STOP:
                return RoutingResponse(
                    conversation_id=state.conversation_id,
                    action=RouterAction.STOP,
                    response=decision.reason,
                )
            assert decision.selected_provider is not None
            try:
                provider_response = await self._dispatcher.dispatch(decision.selected_provider, latest_message)
            except (ProviderUnavailableError, ProviderDisabledError) as exc:
                raise ProviderExecutionError("Unable to obtain a provider response") from exc
            clean_response = self._gateway.process(provider_response)
            state.previous_response = clean_response.response
            state.last_provider = decision.selected_provider
            self._state.update(state)
            return RoutingResponse(
                conversation_id=state.conversation_id,
                action=RouterAction.ANSWER,
                response=clean_response.response,
            )

    async def _decision_from_ladder(
        self, request: RouterRequest, excluded_providers: list[str]
    ) -> RouterDecision:
        for router in (self._primary, self._fallback):
            try:
                raw_decision = await router.decide(request)
                return self._validator.validate(raw_decision, excluded_providers)
            except (Exception, InvalidDecisionError) as exc:
                # Primary and fallback failures deliberately demote exactly one tier.
                logger.warning("router_tier_failed tier=%s error=%s", type(router).__name__, type(exc).__name__)
        default = self._default.select_default(excluded_providers)
        if default is None:
            raise RoutingEngineError("No eligible provider after router failure")
        try:
            return self._validator.validate(default, excluded_providers)
        except InvalidDecisionError as exc:
            raise RoutingEngineError("Default router returned no usable provider") from exc

    def _stop_response(self, conversation_id: str) -> RoutingResponse:
        return RoutingResponse(conversation_id=conversation_id, action=RouterAction.STOP, response=self.STOP_MESSAGE)

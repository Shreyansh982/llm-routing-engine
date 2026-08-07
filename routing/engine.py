"""The deterministic request-lifecycle coordinator."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from control_plane.models import (
    CandidateSet,
    GovernancePolicy,
    PolicyAuditReference,
    PolicyDefinition,
    PolicyKind,
)
from control_plane.repository import PolicyAuditRepository
from control_plane.services import CandidateSetResolver
from core.interfaces import BaseRouter
from gateway.response_gateway import ResponseGateway
from providers.adapter import ProviderUnavailableError
from providers.dispatcher import ProviderDisabledError, ProviderDispatcher
from registry.model_registry import ModelRegistry
from routers.default_router import DeterministicDefaultRouter
from schemas.models import (
    AvailableProvider,
    ChatRequest,
    ConversationState,
    FailureStage,
    LatencyBreakdown,
    RouterAction,
    RouterDecision,
    RouterRequest,
    RoutingDiagnostics,
    RoutingResponse,
    RoutingContext,
    TenantContext,
)
from state.manager import ConversationNotFoundError, ConversationStateManager
from validation.decision_validator import DecisionValidator, InvalidDecisionError

logger = logging.getLogger(__name__)


class RoutingEngineError(RuntimeError):
    code = "ROUTER_FAILURE"

    def __init__(self, message: str, diagnostics: RoutingDiagnostics | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


class ProviderExecutionError(RoutingEngineError):
    code = "PROVIDER_UNAVAILABLE"


class GatewayExecutionError(RoutingEngineError):
    code = "GATEWAY_FAILURE"


class GovernanceDeniedError(RoutingEngineError):
    """A V2 tenant policy left no provider eligible before Router invocation."""

    code = "GOVERNANCE_POLICY_DENIED"


@dataclass
class _DiagnosticsCapture:
    """Request-local timing and metadata capture; it does not affect routing decisions."""

    request_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started: float = field(default_factory=time.perf_counter)
    router_used: str | None = None
    selected_provider: str | None = None
    routing_reason: str | None = None
    capabilities_considered: list[AvailableProvider] = field(default_factory=list)
    fallback_used: bool = False
    router_ms: float = 0.0
    provider_ms: float = 0.0
    gateway_ms: float = 0.0
    provider_error: str | None = None
    tenant: TenantContext | None = None
    candidate_set: CandidateSet | None = None
    policy_audit_recorded: bool = False


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
        candidate_resolver: CandidateSetResolver | None = None,
        policy_audit_repository: PolicyAuditRepository | None = None,
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
        self._candidate_resolver = candidate_resolver
        self._policy_audit_repository = policy_audit_repository

    @property
    def registry(self) -> ModelRegistry:
        """Expose metadata ownership for additive control-plane composition only."""
        return self._registry

    def configure_control_plane(
        self, resolver: CandidateSetResolver, audit_repository: PolicyAuditRepository
    ) -> None:
        """Attach V2.5 services without changing the V1 routing path."""
        self._candidate_resolver = resolver
        self._policy_audit_repository = audit_repository

    async def handle(
        self,
        request: ChatRequest,
        developer_mode: bool = False,
        tenant: TenantContext | None = None,
        routing_context: RoutingContext | None = None,
    ) -> RoutingResponse:
        started = time.perf_counter()
        # V2 policy references are auditable even when developer diagnostics are not
        # returned. V1 still avoids all diagnostics capture unless explicitly requested.
        capture = _DiagnosticsCapture(tenant=tenant) if developer_mode or tenant is not None else None
        tenant_id = tenant.tenant_id if tenant is not None else "legacy"
        routing_context = routing_context or RoutingContext()
        if request.retry:
            state = self._state.load(request.conversation_id, tenant_id)
            stopped = self._prepare_retry(state, tenant_id)
            if stopped:
                result = self._stop_response(state.conversation_id)
                return self._attach_diagnostics(result, state, capture, developer_mode)
        else:
            try:
                state = self._state.load(request.conversation_id, tenant_id)
            except ConversationNotFoundError:
                state = self._state.create(request.conversation_id, request.message, self._max_attempts, tenant_id)

        result = await self._route(state, request.message, capture, tenant_id, routing_context)
        logger.info(
            "routing_complete conversation_id=%s action=%s attempt_count=%s latency_ms=%.2f",
            state.conversation_id,
            result.action,
            state.attempt_count,
            (time.perf_counter() - started) * 1000,
        )
        return self._attach_diagnostics(result, state, capture, developer_mode)

    def conversation_state(self, conversation_id: str, tenant_id: str = "legacy") -> ConversationState:
        """Expose the documented POC debugging view without leaking state ownership."""
        return self._state.load(conversation_id, tenant_id)

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

    async def verify_startup(self) -> None:
        """Verify every configured Router and enabled provider model before serving traffic."""
        router_status = await self.router_health()
        provider_status = await self.provider_health()
        unavailable = [name for name, status in {**router_status, **provider_status}.items() if status != "healthy"]
        if unavailable:
            raise RoutingEngineError(f"Configured Ollama models are unavailable: {', '.join(unavailable)}")

    def _prepare_retry(self, state: ConversationState, tenant_id: str = "legacy") -> bool:
        if self._retry_guard_fired(state):
            return True
        if state.last_provider:
            self._state.exclude_provider(state, state.last_provider, tenant_id)
        # An initial RETRY action has no prior provider to exclude. It remains bounded
        # by the same counter; this is the most consistent interpretation of the spec.
        self._state.increment_attempt(state, tenant_id)
        return self._retry_guard_fired(state)

    def _retry_guard_fired(self, state: ConversationState) -> bool:
        return state.attempt_count >= state.max_attempts or not self._registry.available_for_router(
            state.excluded_providers
        )

    async def _route(
        self,
        state: ConversationState,
        latest_message: str,
        capture: _DiagnosticsCapture | None,
        tenant_id: str,
        routing_context: RoutingContext,
    ) -> RoutingResponse:
        while True:
            if self._retry_guard_fired(state):
                return self._stop_response(state.conversation_id)
            candidate_set = self._resolve_candidates(state.excluded_providers, capture, routing_context)
            if not candidate_set.providers:
                raise GovernanceDeniedError(
                    "No provider is eligible under the active governance policy",
                    diagnostics=self._build_diagnostics(
                        state,
                        capture,
                        failure_level="governance",
                        failure_stage=FailureStage.GOVERNANCE,
                        failure_reason="GOVERNANCE_POLICY_DENIED",
                    ),
                )
            router_request = RouterRequest(
                original_query=state.original_query,
                latest_user_message=latest_message,
                previous_response=state.previous_response,
                attempt=state.attempt_count,
                max_attempts=state.max_attempts,
                excluded_providers=state.excluded_providers,
                available_providers=candidate_set.providers,
            )
            if capture:
                capture.capabilities_considered = router_request.available_providers
            decision = await self._decision_from_ladder(
                router_request,
                state.excluded_providers,
                state,
                capture,
                [provider.id for provider in candidate_set.providers],
            )
            if decision.action == RouterAction.RETRY:
                if self._prepare_retry(state, tenant_id):
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
            if capture:
                capture.selected_provider = decision.selected_provider
            try:
                provider_started = time.perf_counter()
                provider_response = await self._dispatcher.dispatch(decision.selected_provider, latest_message)
                if capture:
                    capture.provider_ms += (time.perf_counter() - provider_started) * 1000
            except (ProviderUnavailableError, ProviderDisabledError) as exc:
                if capture:
                    capture.provider_ms += (time.perf_counter() - provider_started) * 1000
                    # Adapter errors deliberately expose only a stable classification,
                    # never the upstream response body, endpoint, or credential details.
                    capture.provider_error = getattr(exc, "failure_reason", "UNKNOWN")
                raise ProviderExecutionError(
                    "Unable to obtain a provider response",
                    diagnostics=self._build_diagnostics(
                        state,
                        capture,
                        failure_level="provider",
                        failure_stage=FailureStage(getattr(exc, "failure_stage", "provider")),
                        failure_reason=getattr(exc, "failure_reason", "UNKNOWN"),
                        http_status=getattr(exc, "http_status", None),
                    ),
                ) from exc
            try:
                gateway_started = time.perf_counter()
                clean_response = self._gateway.process(provider_response)
                if capture:
                    capture.gateway_ms += (time.perf_counter() - gateway_started) * 1000
            except Exception as exc:
                if capture:
                    capture.gateway_ms += (time.perf_counter() - gateway_started) * 1000
                raise GatewayExecutionError(
                    "Unable to process the provider response",
                    diagnostics=self._build_diagnostics(
                        state,
                        capture,
                        failure_level="gateway",
                        failure_stage=FailureStage.GATEWAY,
                        failure_reason="UNKNOWN",
                    ),
                ) from exc
            state.previous_response = clean_response.response
            state.last_provider = decision.selected_provider
            self._state.update(state, tenant_id)
            return RoutingResponse(
                conversation_id=state.conversation_id,
                action=RouterAction.ANSWER,
                response=clean_response.response,
            )

    async def _decision_from_ladder(
        self,
        request: RouterRequest,
        excluded_providers: list[str],
        state: ConversationState,
        capture: _DiagnosticsCapture | None,
        allowed_provider_ids: list[str] | None = None,
    ) -> RouterDecision:
        for router_name, router, fallback_used in (
            ("primary_router", self._primary, False),
            ("fallback_router", self._fallback, True),
        ):
            router_started = time.perf_counter()
            if capture:
                capture.router_used = router_name
                capture.fallback_used = capture.fallback_used or fallback_used
            try:
                raw_decision = await router.decide(request)
            except Exception as exc:
                if capture:
                    capture.router_ms += (time.perf_counter() - router_started) * 1000
                # Primary and fallback failures deliberately demote exactly one tier.
                logger.warning("router_tier_failed tier=%s error=%s", type(router).__name__, type(exc).__name__)
                continue
            try:
                decision = self._validator.validate(raw_decision, excluded_providers, allowed_provider_ids)
            except InvalidDecisionError as exc:
                if capture:
                    capture.router_ms += (time.perf_counter() - router_started) * 1000
                logger.warning("router_tier_invalid tier=%s error=%s", type(router).__name__, type(exc).__name__)
                continue
            if capture:
                capture.router_ms += (time.perf_counter() - router_started) * 1000
                capture.routing_reason = decision.reason
            return decision
        if capture:
            capture.router_used = "deterministic_default_router"
            capture.fallback_used = True
        default = self._default.select_default(excluded_providers, allowed_provider_ids)
        if default is None:
            raise RoutingEngineError(
                "No eligible provider after router failure",
                diagnostics=self._build_diagnostics(
                    state,
                    capture,
                    failure_level="router",
                    failure_stage=FailureStage.DEFAULT_ROUTER,
                    failure_reason="UNKNOWN",
                ),
            )
        try:
            decision = self._validator.validate(default, excluded_providers, allowed_provider_ids)
            if capture:
                capture.routing_reason = decision.reason
            return decision
        except InvalidDecisionError as exc:
            raise RoutingEngineError(
                "Default router returned no usable provider",
                diagnostics=self._build_diagnostics(
                    state,
                    capture,
                    failure_level="router",
                    failure_stage=FailureStage.VALIDATOR,
                    failure_reason="SCHEMA_VALIDATION_FAILED",
                ),
            ) from exc

    def _stop_response(self, conversation_id: str) -> RoutingResponse:
        return RoutingResponse(conversation_id=conversation_id, action=RouterAction.STOP, response=self.STOP_MESSAGE)

    def _attach_diagnostics(
        self,
        response: RoutingResponse,
        state: ConversationState,
        capture: _DiagnosticsCapture | None,
        developer_mode: bool,
    ) -> RoutingResponse:
        if capture is None:
            return response
        diagnostics = self._build_diagnostics(state, capture)
        if not developer_mode:
            return response
        return response.model_copy(update={"diagnostics": diagnostics})

    def _resolve_candidates(
        self,
        excluded_providers: list[str],
        capture: _DiagnosticsCapture | None,
        routing_context: RoutingContext,
    ) -> CandidateSet:
        if self._candidate_resolver is None or capture is None or capture.tenant is None:
            providers = self._registry.available_for_router(excluded_providers)
            # V1 does not use policy/governance. The placeholder is never surfaced.
            return CandidateSet(
                providers=providers,
                policy=PolicyDefinition(
                    tenant_id="legacy", policy_id="legacy-balanced", kind=PolicyKind.LEGACY_BALANCED
                ),
                governance=GovernancePolicy(tenant_id="legacy"),
            )
        candidate_set = self._candidate_resolver.resolve(capture.tenant, routing_context, excluded_providers)
        capture.candidate_set = candidate_set
        self._record_policy_audit(capture)
        return candidate_set

    def _record_policy_audit(self, capture: _DiagnosticsCapture) -> None:
        if (
            capture.policy_audit_recorded
            or capture.candidate_set is None
            or capture.tenant is None
            or self._policy_audit_repository is None
        ):
            return
        candidate_set = capture.candidate_set
        self._policy_audit_repository.append(
            PolicyAuditReference(
                request_id=capture.request_id,
                tenant_id=capture.tenant.tenant_id,
                policy_id=candidate_set.policy.policy_id,
                policy_version=candidate_set.policy.version,
                governance_policy_id=candidate_set.governance.policy_id,
                governance_policy_version=candidate_set.governance.version,
                candidate_ids=tuple(provider.id for provider in candidate_set.providers),
                exclusions=tuple(candidate_set.exclusions),
                rankings=tuple(candidate_set.rankings),
            )
        )
        capture.policy_audit_recorded = True

    def _build_diagnostics(
        self,
        state: ConversationState,
        capture: _DiagnosticsCapture | None,
        failure_level: str | None = None,
        failure_stage: FailureStage = FailureStage.NONE,
        failure_reason: str = "NONE",
        http_status: int | None = None,
    ) -> RoutingDiagnostics | None:
        if capture is None:
            return None
        backend = model = None
        if capture.selected_provider:
            provider = self._registry.get_provider(capture.selected_provider)
            backend, model = provider.backend, provider.model
        candidate_set = capture.candidate_set
        return RoutingDiagnostics(
            developer_mode=True,
            request_id=capture.request_id,
            timestamp=capture.timestamp,
            request_timestamp=capture.timestamp,
            completed_at=datetime.now(timezone.utc),
            router_used=capture.router_used,
            selected_provider=capture.selected_provider,
            backend=backend,
            model=model,
            provider_backend=backend,
            configured_model=model,
            routing_reason=capture.routing_reason,
            capabilities_considered=capture.capabilities_considered,
            fallback_used=capture.fallback_used,
            retry_count=state.attempt_count,
            latency_breakdown=LatencyBreakdown(
                router_ms=capture.router_ms,
                provider_ms=capture.provider_ms,
                gateway_ms=capture.gateway_ms,
                total_ms=(time.perf_counter() - capture.started) * 1000,
            ),
            http_status=http_status,
            failure_stage=failure_stage,
            failure_reason=failure_reason,
            failure_level=failure_level,
            provider_error=capture.provider_error,
            tenant_id=capture.tenant.tenant_id if capture.tenant else None,
            policy_id=candidate_set.policy.policy_id if candidate_set else None,
            policy_version=candidate_set.policy.version if candidate_set else None,
            governance_policy_id=candidate_set.governance.policy_id if candidate_set else None,
            governance_policy_version=candidate_set.governance.version if candidate_set else None,
            policy_candidate_ids=[provider.id for provider in candidate_set.providers] if candidate_set else [],
            governance_exclusions=[item.model_dump() for item in candidate_set.exclusions] if candidate_set else [],
        )

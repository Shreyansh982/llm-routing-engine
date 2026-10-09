"""The deterministic request-lifecycle coordinator."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from control_plane.models import CandidateSet, GovernancePolicy, PolicyAuditReference, PolicyDefinition, PolicyKind
from control_plane.repository import PolicyAuditRepository
from control_plane.services import CandidateSetResolver, PolicyNotFoundError
from core.interfaces import BaseRouter
from gateway.response_gateway import ResponseGateway
from providers.adapter import ProviderUnavailableError
from providers.dispatcher import ProviderDisabledError, ProviderDispatcher
from providers.model_catalog import StartupModelVerifier
from registry.model_registry import ModelRegistry
from schemas.models import (
    failure_class,
    AvailableProvider,
    ChatRequest,
    ConversationState,
    FailureStage,
    LatencyBreakdown,
    ProviderConfig,
    ProviderRequest,
    RouterAction,
    RouterDecision,
    RouterRequest,
    RoutingContext,
    RoutingDiagnostics,
    RoutingResponse,
    TaskGroup,
    TenantContext,
)
from state.manager import ConversationNotFoundError, ConversationStateManager
from validation.decision_validator import DecisionValidator

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


class DeadlineExceededError(RoutingEngineError):
    code = "DEADLINE_EXCEEDED"


class UnexpectedExecutionError(RoutingEngineError):
    code = "INTERNAL_ERROR"


class InvalidRoutingRequestError(RoutingEngineError):
    code = "INVALID_REQUEST"


class GovernanceDeniedError(RoutingEngineError):
    code = "GOVERNANCE_POLICY_DENIED"


@dataclass
class _DiagnosticsCapture:
    request_id: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started: float = field(default_factory=time.perf_counter)
    router_used: str | None = None
    selected_provider: str | None = None
    initial_provider: str | None = None
    task_group: TaskGroup | None = None
    routing_reason: str | None = None
    capabilities_considered: list[AvailableProvider] = field(default_factory=list)
    router_attempts: list[dict[str, object]] = field(default_factory=list)
    provider_attempts: list[dict[str, object]] = field(default_factory=list)
    fallback_used: bool = False
    router_ms: float = 0.0
    provider_ms: float = 0.0
    gateway_ms: float = 0.0
    provider_error: str | None = None
    tenant: TenantContext | None = None
    candidate_set: CandidateSet | None = None
    policy_audit_recorded: bool = False
    state: ConversationState | None = None
    stage: FailureStage = FailureStage.REQUEST
    deadline: float = 0.0


class RoutingEngine:
    """Coordinates task classification, deterministic fallback, and response handling."""

    STOP_MESSAGE = "Unable to produce a better response."
    CLARIFY_MESSAGE = "Could you clarify what you would like help with?"

    def __init__(
        self,
        state_manager: ConversationStateManager,
        registry: ModelRegistry,
        primary_router: BaseRouter,
        fallback_router: BaseRouter,
        validator: DecisionValidator,
        dispatcher: ProviderDispatcher,
        gateway: ResponseGateway,
        max_attempts: int,
        candidate_resolver: CandidateSetResolver | None = None,
        policy_audit_repository: PolicyAuditRepository | None = None,
        startup_model_verifier: StartupModelVerifier | None = None,
        overall_request_timeout: float = 60.0,
    ) -> None:
        self._state = state_manager
        self._registry = registry
        self._primary = primary_router
        self._fallback = fallback_router
        self._validator = validator
        self._dispatcher = dispatcher
        self._gateway = gateway
        self._max_attempts = max_attempts
        self._candidate_resolver = candidate_resolver
        self._policy_audit_repository = policy_audit_repository
        self._startup_model_verifier = startup_model_verifier
        self._overall_request_timeout = overall_request_timeout

    @property
    def registry(self) -> ModelRegistry:
        return self._registry

    def configure_control_plane(
        self, resolver: CandidateSetResolver, audit_repository: PolicyAuditRepository
    ) -> None:
        self._candidate_resolver = resolver
        self._policy_audit_repository = audit_repository

    async def handle(
        self,
        request: ChatRequest,
        developer_mode: bool = False,
        tenant: TenantContext | None = None,
        routing_context: RoutingContext | None = None,
        request_id: str | None = None,
    ) -> RoutingResponse:
        capture = _DiagnosticsCapture(request_id=request_id or str(uuid4()), tenant=tenant)
        capture.deadline = asyncio.get_running_loop().time() + self._overall_request_timeout
        try:
            async with asyncio.timeout_at(capture.deadline):
                return await self._handle(request, developer_mode, tenant, routing_context, capture)
        except TimeoutError as exc:
            raise DeadlineExceededError(
                "Request deadline exceeded",
                self._build_diagnostics(capture.state, capture, failure_level="deadline",
                                        failure_stage=FailureStage.DEADLINE,
                                        failure_reason="DEADLINE_EXCEEDED"),
            ) from exc
        except (RoutingEngineError, ConversationNotFoundError, PolicyNotFoundError):
            raise
        except Exception as exc:
            logger.error("routing_unexpected_failure request_id=%s stage=%s", capture.request_id, capture.stage)
            raise UnexpectedExecutionError(
                "Unable to process request",
                self._build_diagnostics(capture.state, capture, failure_level=capture.stage.value,
                                        failure_stage=capture.stage, failure_reason="UNKNOWN"),
            ) from exc

    async def _handle(
        self, request: ChatRequest, developer_mode: bool, tenant: TenantContext | None,
        routing_context: RoutingContext | None, capture: _DiagnosticsCapture,
    ) -> RoutingResponse:
        started = time.perf_counter()
        tenant_id = tenant.tenant_id if tenant is not None else "legacy"
        routing_context = routing_context or RoutingContext()
        if request.retry:
            state = self._state.load(request.conversation_id, tenant_id)
            capture.state = state
            if state.has_image and request.image is None:
                raise InvalidRoutingRequestError(
                    "Image retries require the image to be attached again",
                    self._build_diagnostics(state, capture, failure_level="request",
                                            failure_stage=FailureStage.REQUEST,
                                            failure_reason="IMAGE_REQUIRED"),
                )
            state.has_image = request.image is not None
            if self._prepare_retry(state, tenant_id):
                return self._attach_diagnostics(
                    self._stop_response(state.conversation_id), state, capture, developer_mode
                )
        else:
            try:
                state = self._state.load(request.conversation_id, tenant_id)
            except ConversationNotFoundError:
                state = self._state.create(
                    request.conversation_id, request.message, self._max_attempts, tenant_id
                )
            state.original_query = request.message
            state.attempt_count = 0
            state.excluded_providers = []
            state.last_provider = None
            state.has_image = request.image is not None
            self._state.update(state, tenant_id)

        capture.state = state
        result = await self._route(state, request, capture, tenant_id, routing_context)
        logger.info(
            "routing_complete request_id=%s conversation_id=%s action=%s attempt_count=%s latency_ms=%.2f",
            capture.request_id,
            state.conversation_id,
            result.action,
            state.attempt_count,
            (time.perf_counter() - started) * 1000,
        )
        return self._attach_diagnostics(result, state, capture, developer_mode)

    def conversation_state(self, conversation_id: str, tenant_id: str = "legacy") -> ConversationState:
        return self._state.load(conversation_id, tenant_id)

    def provider_summaries(self) -> list[dict[str, object]]:
        return [{"id": provider.id, "enabled": provider.enabled} for provider in self._registry.list_providers()]

    async def router_health(self) -> dict[str, object]:
        if self._startup_model_verifier is not None:
            results = await self._startup_model_verifier.verify(role="router")
            return {**{item.name: "healthy" if item.available else "unhealthy" for item in results},
                    "backends": self._dispatcher.circuit_snapshot()}
        return {
            "primary_router": await self._primary.health(),
            "fallback_router": await self._fallback.health(),
            "backends": self._dispatcher.circuit_snapshot(),
        }

    async def provider_health(self) -> dict[str, object]:
        if self._startup_model_verifier is not None:
            results = await self._startup_model_verifier.verify(role="provider")
            disabled = {provider.id for provider in self._registry.list_providers() if not provider.enabled}
            return {"providers": {item.name: "offline" if item.name in disabled else "healthy" if item.available else "unhealthy" for item in results},
                    "backends": self._dispatcher.circuit_snapshot()}
        return {
            "providers": await self._dispatcher.health(),
            "backends": self._dispatcher.circuit_snapshot(),
        }

    async def verify_startup(self) -> None:
        if self._startup_model_verifier is not None:
            results = await self._startup_model_verifier.verify()
            backend_summaries = []
            for backend in dict.fromkeys(result.backend for result in results):
                backend_results = [result for result in results if result.backend == backend]
                available = sum(result.available for result in backend_results)
                backend_summaries.append(f"{backend}: {available}/{len(backend_results)} available")
            router_results = [result for result in results if result.role == "router"]
            logger.info(
                "Provider model verification: %s; routers: %s/%s available",
                "; ".join(backend_summaries),
                sum(result.available for result in router_results),
                len(router_results),
            )
            unavailable = [result for result in results if not result.available]
            if unavailable:
                details = []
                for result in unavailable:
                    status = (
                        f", models_http_status={result.http_status}"
                        if result.http_status is not None
                        else ""
                    )
                    details.append(
                        f"{result.name} [backend={result.backend}, "
                        f"model={result.configured_model}, reason={result.reason}, "
                        f"catalog_match={result.catalog_model or 'none'}{status}]"
                    )
                raise RoutingEngineError(
                    "Configured models are unavailable: " + "; ".join(details)
                )
            return

        router_status = await self.router_health()
        provider_health = await self.provider_health()
        statuses = {
            "primary_router": router_status["primary_router"],
            "fallback_router": router_status["fallback_router"],
            **provider_health["providers"],
        }
        unavailable = [name for name, status in statuses.items() if status != "healthy"]
        if unavailable:
            raise RoutingEngineError(
                f"Configured router/provider models are unavailable: {', '.join(unavailable)}"
            )

    def _prepare_retry(self, state: ConversationState, tenant_id: str) -> bool:
        if self._retry_guard_fired(state):
            return True
        if state.last_provider:
            self._state.exclude_provider(state, state.last_provider, tenant_id)
        self._state.increment_attempt(state, tenant_id)
        return self._retry_guard_fired(state)

    def _retry_guard_fired(self, state: ConversationState) -> bool:
        return state.attempt_count >= state.max_attempts or not self._registry.available_for_router(
            state.excluded_providers
        )

    async def _route(
        self,
        state: ConversationState,
        request: ChatRequest,
        capture: _DiagnosticsCapture | None,
        tenant_id: str,
        routing_context: RoutingContext,
    ) -> RoutingResponse:
        blocked_backends: set[str] = set()
        while True:
            if self._retry_guard_fired(state):
                return self._stop_response(state.conversation_id)
            if capture:
                capture.stage = FailureStage.GOVERNANCE
            candidate_set = self._resolve_candidates(state.excluded_providers, capture, routing_context)
            allowed_ids = [provider.id for provider in candidate_set.providers]
            if not allowed_ids:
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
            available_groups = self._registry.available_task_groups(
                allowed_ids, requires_image=request.image is not None
            )
            router_request = RouterRequest(
                retry=request.retry,
                original_query=state.original_query,
                latest_user_message=request.message,
                previous_response=state.previous_response,
                attempt=state.attempt_count,
                max_attempts=state.max_attempts,
                has_image=request.image is not None,
                available_task_groups=available_groups,
            )
            if capture:
                capture.capabilities_considered = candidate_set.providers
            decision, router_blocked_backends = await self._decision_from_ladder(
                router_request, state, capture
            )
            blocked_backends.update(router_blocked_backends)
            if decision.action == RouterAction.RETRY:
                if self._prepare_retry(state, tenant_id):
                    return self._stop_response(state.conversation_id)
                continue
            if decision.action == RouterAction.CLARIFY:
                return RoutingResponse(
                    conversation_id=state.conversation_id,
                    action=RouterAction.CLARIFY,
                    response=self.CLARIFY_MESSAGE,
                )
            if decision.action == RouterAction.STOP:
                return RoutingResponse(
                    conversation_id=state.conversation_id,
                    action=RouterAction.STOP,
                    response=self.STOP_MESSAGE,
                )
            assert decision.task_group is not None
            if capture:
                capture.task_group = decision.task_group
            candidates = self._registry.candidates_for(
                decision.task_group,
                requires_image=request.image is not None,
                excluded_provider_ids=state.excluded_providers,
                allowed_provider_ids=allowed_ids,
                preserve_allowed_order=capture is not None and capture.tenant is not None,
            )
            return await self._execute_with_fallback(
                candidates,
                ProviderRequest(text=request.message, image=request.image),
                state,
                capture,
                tenant_id,
                blocked_backends,
            )

    async def _execute_with_fallback(
        self,
        candidates: list[ProviderConfig],
        request: ProviderRequest,
        state: ConversationState,
        capture: _DiagnosticsCapture | None,
        tenant_id: str,
        blocked_backends: set[str],
    ) -> RoutingResponse:
        last_error: Exception | None = None
        if capture and candidates:
            capture.initial_provider = candidates[0].id
        for index, candidate in enumerate(candidates):
            if capture and asyncio.get_running_loop().time() >= capture.deadline:
                raise TimeoutError
            if candidate.backend in blocked_backends:
                if capture:
                    capture.provider_attempts.append(
                        {
                            "provider_id": candidate.id,
                            "backend": candidate.backend,
                            "model": candidate.model,
                            "outcome": "skipped_auth_failure",
                            "failure_class": "AUTH",
                            "upstream_status": None,
                        }
                    )
                continue
            provider_started = time.perf_counter()
            if capture:
                capture.stage = FailureStage.DISPATCHER
            try:
                provider_response = await self._dispatcher.dispatch(candidate.id, request)
            except asyncio.CancelledError:
                if capture:
                    elapsed = (time.perf_counter() - provider_started) * 1000
                    capture.provider_ms += elapsed
                    capture.provider_attempts.append({
                        "provider_id": candidate.id, "backend": candidate.backend,
                        "model": candidate.model, "outcome": "cancelled",
                        "failure_class": "DEADLINE_EXCEEDED" if asyncio.get_running_loop().time() >= capture.deadline else "CANCELLED",
                        "upstream_status": None, "latency_ms": elapsed,
                    })
                    if asyncio.get_running_loop().time() >= capture.deadline:
                        capture.provider_attempts.extend({
                            "provider_id": pending.id, "backend": pending.backend,
                            "model": pending.model, "outcome": "skipped_deadline",
                            "failure_class": "DEADLINE_EXCEEDED", "upstream_status": None,
                        } for pending in candidates[index + 1:])
                raise
            except (ProviderUnavailableError, ProviderDisabledError) as exc:
                last_error = exc
                if getattr(exc, "blocks_backend", False):
                    blocked_backends.add(candidate.backend)
                if capture:
                    capture.provider_ms += (time.perf_counter() - provider_started) * 1000
                    capture.provider_error = getattr(exc, "failure_reason", "UNKNOWN")
                    capture.provider_attempts.append(
                        {
                            "provider_id": candidate.id,
                            "backend": candidate.backend,
                            "model": candidate.model,
                            "outcome": "failed",
                            "failure_reason": getattr(exc, "failure_reason", "UNKNOWN"),
                            "failure_scope": str(getattr(exc, "failure_scope", "non_transient")),
                            "upstream_status": getattr(exc, "http_status", None),
                            "failure_class": failure_class(exc),
                            "latency_ms": (time.perf_counter() - provider_started) * 1000,
                        }
                    )
                    capture.fallback_used = True
                continue
            except Exception:
                if capture:
                    capture.provider_attempts.append({
                        "provider_id": candidate.id, "backend": candidate.backend,
                        "model": candidate.model, "outcome": "failed",
                        "failure_class": "UNKNOWN", "upstream_status": None,
                        "latency_ms": (time.perf_counter() - provider_started) * 1000,
                    })
                raise
            if capture:
                capture.provider_ms += (time.perf_counter() - provider_started) * 1000
                capture.provider_attempts.append(
                    {
                        "provider_id": candidate.id,
                        "backend": candidate.backend,
                        "model": candidate.model,
                        "outcome": "success",
                        "upstream_status": None,
                        "failure_class": None,
                        "latency_ms": (time.perf_counter() - provider_started) * 1000,
                    }
                )
                capture.selected_provider = candidate.id
                capture.fallback_used = capture.fallback_used or len(capture.provider_attempts) > 1
            try:
                if capture:
                    capture.stage = FailureStage.GATEWAY
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
            if capture and asyncio.get_running_loop().time() >= capture.deadline:
                raise TimeoutError
            state.previous_response = clean_response.response
            state.last_provider = candidate.id
            self._state.update(state, tenant_id)
            return RoutingResponse(
                conversation_id=state.conversation_id,
                action=RouterAction.ANSWER,
                response=clean_response.response,
            )

        failure_reason = getattr(last_error, "failure_reason", "NO_ELIGIBLE_PROVIDER")
        raise ProviderExecutionError(
            "All eligible task providers are unavailable",
            diagnostics=self._build_diagnostics(
                state,
                capture,
                failure_level="provider",
                failure_stage=FailureStage.PROVIDER,
                failure_reason=failure_reason,
                upstream_status=getattr(last_error, "http_status", None),
            ),
        )

    async def _decision_from_ladder(
        self,
        request: RouterRequest,
        state: ConversationState,
        capture: _DiagnosticsCapture | None,
    ) -> tuple[RouterDecision, set[str]]:
        blocked_backends: set[str] = set()
        for router_name, router in (
            ("primary_router", self._primary),
            ("fallback_router", self._fallback),
        ):
            if capture:
                capture.stage = FailureStage.PRIMARY_ROUTER if router_name == "primary_router" else FailureStage.FALLBACK_ROUTER
            router_started = time.perf_counter()
            try:
                raw_decision = await router.decide(request)
                decision = self._validator.validate(
                    raw_decision, request.available_task_groups, request.has_image, request.retry
                )
            except asyncio.CancelledError:
                if capture:
                    elapsed = (time.perf_counter() - router_started) * 1000
                    capture.router_ms += elapsed
                    capture.router_attempts.append({
                        "router": router_name, "outcome": "cancelled", "latency_ms": elapsed,
                        "upstream_status": None,
                        "failure_class": "DEADLINE_EXCEEDED" if asyncio.get_running_loop().time() >= capture.deadline else "CANCELLED",
                    })
                raise
            except Exception as exc:
                if getattr(exc, "blocks_backend", False) and getattr(exc, "backend", None):
                    blocked_backends.add(exc.backend)
                elapsed = (time.perf_counter() - router_started) * 1000
                if capture:
                    capture.router_ms += elapsed
                    capture.router_attempts.append(
                        {
                            "router": router_name,
                            "outcome": "failed",
                            "latency_ms": elapsed,
                            "failure_reason": getattr(exc, "failure_reason", "INVALID_DECISION" if isinstance(exc, ValueError) else "UNKNOWN"),
                            "failure_class": failure_class(exc),
                            "upstream_status": getattr(exc, "http_status", None),
                        }
                    )
                    capture.fallback_used = True
                logger.warning("router_tier_failed request_id=%s tier=%s error=%s",
                               capture.request_id if capture else None, router_name, type(exc).__name__)
                continue
            elapsed = (time.perf_counter() - router_started) * 1000
            if capture:
                capture.router_ms += elapsed
                capture.router_used = router_name
                capture.routing_reason = decision.reason
                capture.router_attempts.append(
                    {"router": router_name, "outcome": "success", "latency_ms": elapsed,
                     "upstream_status": None, "failure_class": None}
                )
            return decision, blocked_backends
        raise RoutingEngineError(
            "Both configured routers failed",
            diagnostics=self._build_diagnostics(
                state,
                capture,
                failure_level="router",
                failure_stage=FailureStage.FALLBACK_ROUTER,
                failure_reason="ROUTER_CHAIN_EXHAUSTED",
            ),
        )

    def _stop_response(self, conversation_id: str) -> RoutingResponse:
        return RoutingResponse(
            conversation_id=conversation_id,
            action=RouterAction.STOP,
            response=self.STOP_MESSAGE,
        )

    def _attach_diagnostics(
        self,
        response: RoutingResponse,
        state: ConversationState,
        capture: _DiagnosticsCapture | None,
        developer_mode: bool,
    ) -> RoutingResponse:
        if capture is None or not developer_mode:
            return response
        return response.model_copy(update={"diagnostics": self._build_diagnostics(state, capture)})

    def _resolve_candidates(
        self,
        excluded_providers: list[str],
        capture: _DiagnosticsCapture | None,
        routing_context: RoutingContext,
    ) -> CandidateSet:
        if self._candidate_resolver is None or capture is None or capture.tenant is None:
            return CandidateSet(
                providers=self._registry.available_for_router(excluded_providers),
                policy=PolicyDefinition(
                    tenant_id="legacy", policy_id="legacy-balanced", kind=PolicyKind.LEGACY_BALANCED
                ),
                governance=GovernancePolicy(tenant_id="legacy"),
            )
        candidate_set = self._candidate_resolver.resolve(
            capture.tenant, routing_context, excluded_providers
        )
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
        state: ConversationState | None,
        capture: _DiagnosticsCapture | None,
        failure_level: str | None = None,
        failure_stage: FailureStage = FailureStage.NONE,
        failure_reason: str = "NONE",
        upstream_status: int | None = None,
    ) -> RoutingDiagnostics | None:
        if capture is None:
            return None
        backend = model = None
        if capture.selected_provider:
            provider = self._registry.get_provider(capture.selected_provider)
            backend, model = provider.backend, provider.model
        candidate_set = capture.candidate_set
        if upstream_status is None:
            upstream_status = next((attempt["upstream_status"] for attempt in reversed(
                capture.router_attempts + capture.provider_attempts
            ) if attempt.get("upstream_status") is not None), None)
        terminal_class = None
        if failure_reason != "NONE":
            terminal_class = next((attempt["failure_class"] for attempt in reversed(
                capture.router_attempts + capture.provider_attempts
            ) if attempt.get("failure_class") is not None), "UNKNOWN")
        return RoutingDiagnostics(
            developer_mode=True,
            request_id=capture.request_id,
            timestamp=capture.timestamp,
            request_timestamp=capture.timestamp,
            completed_at=datetime.now(timezone.utc),
            router_used=capture.router_used,
            selected_provider=capture.selected_provider,
            provider_id=capture.selected_provider,
            backend=backend,
            model=model,
            provider_backend=backend,
            configured_model=model,
            routing_reason=capture.routing_reason,
            task_group=capture.task_group,
            router_attempts=capture.router_attempts,
            initial_provider=capture.initial_provider,
            final_provider=capture.selected_provider,
            fallback_path=capture.provider_attempts,
            fallback_count=max(len(capture.provider_attempts) - 1, 0),
            circuit_state=self._dispatcher.circuit_snapshot(),
            capabilities_considered=capture.capabilities_considered,
            fallback_used=capture.fallback_used,
            router_fallback_used=any(item["router"] == "fallback_router" for item in capture.router_attempts),
            provider_fallback_used=len(capture.provider_attempts) > 1,
            retry_count=state.attempt_count if state else 0,
            latency_breakdown=LatencyBreakdown(
                router_ms=capture.router_ms,
                provider_ms=capture.provider_ms,
                gateway_ms=capture.gateway_ms,
                total_ms=(time.perf_counter() - capture.started) * 1000,
            ),
            upstream_status=upstream_status,
            upstream_http_status=upstream_status,
            failure_class="DEADLINE_EXCEEDED" if failure_reason == "DEADLINE_EXCEEDED" else terminal_class,
            failure_stage=failure_stage,
            failure_reason=failure_reason,
            failure_level=failure_level,
            provider_error=capture.provider_error,
            tenant_id=capture.tenant.tenant_id if capture.tenant else None,
            policy_id=candidate_set.policy.policy_id if candidate_set else None,
            policy_version=candidate_set.policy.version if candidate_set else None,
            governance_policy_id=candidate_set.governance.policy_id if candidate_set else None,
            governance_policy_version=candidate_set.governance.version if candidate_set else None,
            policy_candidate_ids=[provider.id for provider in candidate_set.providers] if candidate_set else None,
            governance_exclusions=[item.model_dump() for item in candidate_set.exclusions] if candidate_set else None,
        )

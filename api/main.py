"""Versioned FastAPI entry point with no routing decisions of its own."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from config.settings import Settings, get_settings
from gateway.response_gateway import ResponseGateway
from providers.dispatcher import ProviderDispatcher
from registry.model_registry import ModelRegistry
from routers.default_router import DeterministicDefaultRouter
from routers.http_router import FallbackRouter, PrimaryRouter
from routing.engine import GatewayExecutionError, ProviderExecutionError, RoutingEngine, RoutingEngineError
from schemas.models import (
    ChatRequest,
    ErrorEnvelope,
    FailureStage,
    LatencyBreakdown,
    RoutingDiagnostics,
    RoutingResponse,
    SuccessEnvelope,
)
from state.manager import ConversationNotFoundError, ConversationStateManager
from validation.decision_validator import DecisionValidator


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def build_engine(settings: Settings) -> RoutingEngine:
    registry = ModelRegistry(settings.provider_configs())
    identity_terms = [term for provider in registry.list_providers() for term in provider.identity_terms]
    state = ConversationStateManager()
    return RoutingEngine(
        state_manager=state,
        registry=registry,
        primary_router=PrimaryRouter(settings.primary_router_config()),
        fallback_router=FallbackRouter(settings.fallback_router_config()),
        default_router=DeterministicDefaultRouter(registry),
        validator=DecisionValidator(registry),
        dispatcher=ProviderDispatcher(registry, settings.request_timeout),
        gateway=ResponseGateway(identity_terms),
        max_attempts=settings.max_retries,
    )


def _success(data: dict[str, object]) -> dict[str, object]:
    return SuccessEnvelope(data=data).model_dump()


def _error(code: str, message: str, failure_level: str | None = None) -> dict[str, object]:
    return ErrorEnvelope(
        error={"code": code, "message": message, "failure_level": failure_level}
    ).model_dump(exclude_none=True)


def _developer_mode(request: Request) -> bool:
    return request.headers.get("X-Developer-Mode", "").strip().lower() in {"true", "1"}


def _request_failure_diagnostics(status_code: int, failure_level: str) -> RoutingDiagnostics:
    now = datetime.now(timezone.utc)
    return RoutingDiagnostics(
        developer_mode=True,
        request_id=str(uuid4()),
        timestamp=now,
        request_timestamp=now,
        completed_at=now,
        retry_count=0,
        latency_breakdown=LatencyBreakdown(router_ms=0, provider_ms=0, gateway_ms=0, total_ms=0),
        http_status=status_code,
        failure_stage=FailureStage.NONE,
        failure_reason="UNKNOWN",
        failure_level=failure_level,
    )


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    failure_level: str,
    diagnostics: RoutingDiagnostics | None = None,
) -> JSONResponse:
    content = _error(code, message, failure_level if _developer_mode(request) else None)
    if _developer_mode(request):
        diagnostic = diagnostics or _request_failure_diagnostics(status_code, failure_level)
        upstream_status = diagnostic.http_status
        diagnostic = diagnostic.model_copy(
            update={
                "http_status": upstream_status if upstream_status is not None else status_code,
                "failure_level": failure_level,
                "completed_at": datetime.now(timezone.utc),
            }
        )
        content["diagnostics"] = diagnostic.model_dump(mode="json", exclude_none=True)
    return JSONResponse(status_code=status_code, content=content)


def _provider_failure_diagnostics(diagnostics: RoutingDiagnostics | None) -> RoutingDiagnostics | None:
    """Publish provider-error metadata before the controlled API error is formed.

    The Routing Engine has already captured this information. Keeping the enrichment at
    the API boundary preserves the frozen engine, retry, and gateway responsibilities.
    """
    if diagnostics is None:
        return None
    return diagnostics.model_copy(
        update={
            "provider_id": diagnostics.selected_provider,
            "upstream_http_status": diagnostics.http_status,
        }
    )


def _provider_diagnostics(settings: Settings) -> dict[str, object]:
    """Safe configuration metadata for developer-only API responses; never includes keys."""
    return {
        "configured_providers": [
            {
                "id": provider.id,
                "backend": provider.backend,
                "model": provider.model,
                "endpoint": provider.endpoint,
                "timeout": provider.timeout,
                "capabilities": provider.capabilities.model_dump(),
            }
            for provider in settings.provider_configs()
        ],
        "retry_limit": settings.max_retries,
    }


def _router_diagnostics(settings: Settings) -> dict[str, object]:
    return {
        "configured_routers": [
            {"id": "primary_router", **settings.primary_router_config().model_dump(exclude={"api_key"})},
            {"id": "fallback_router", **settings.fallback_router_config().model_dump(exclude={"api_key"})},
        ]
    }


def create_app(engine: RoutingEngine | None = None, settings: Settings | None = None) -> FastAPI:
    configure_logging()
    runtime_settings = settings or get_settings()
    routing_engine = engine or build_engine(runtime_settings)
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await routing_engine.verify_startup()
        yield

    app = FastAPI(
        title="Provider-Agnostic LLM Routing Engine", version="0.1.0", lifespan=lifespan
    )
    app.state.engine = routing_engine

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request, _: RequestValidationError) -> JSONResponse:
        return _error_response(request, 422, "INVALID_REQUEST", "Request validation failed.", "request")

    @app.exception_handler(ConversationNotFoundError)
    async def conversation_handler(request: Request, _: ConversationNotFoundError) -> JSONResponse:
        return _error_response(request, 404, "INVALID_CONVERSATION", "Conversation not found.", "request")

    @app.exception_handler(ProviderExecutionError)
    async def provider_handler(request: Request, exc: ProviderExecutionError) -> JSONResponse:
        return _error_response(
            request,
            503,
            "PROVIDER_UNAVAILABLE",
            "Selected provider is unavailable.",
            "provider",
            _provider_failure_diagnostics(exc.diagnostics),
        )

    @app.exception_handler(RoutingEngineError)
    async def routing_handler(request: Request, exc: RoutingEngineError) -> JSONResponse:
        return _error_response(
            request, 503, "ROUTER_FAILURE", "Unable to process request.", "router", exc.diagnostics
        )

    @app.exception_handler(GatewayExecutionError)
    async def gateway_handler(request: Request, exc: GatewayExecutionError) -> JSONResponse:
        return _error_response(
            request, 503, "GATEWAY_FAILURE", "Unable to process provider response.", "gateway", exc.diagnostics
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, _: Exception) -> JSONResponse:
        # Developer Mode promises an observability envelope even for an unforeseen
        # HTTP failure. Normal users still receive the established error envelope.
        return _error_response(request, 500, "INTERNAL_ERROR", "Unable to process request.", "unknown")

    @app.post("/api/v1/chat")
    async def chat(http_request: Request, request: ChatRequest) -> dict[str, object]:
        result: RoutingResponse = await app.state.engine.handle(
            request, developer_mode=_developer_mode(http_request)
        )
        data = result.model_dump(mode="json", exclude_none=True)
        if _developer_mode(http_request) and isinstance(data.get("diagnostics"), dict):
            data["diagnostics"]["http_status"] = 200
        return _success(data)

    @app.get("/api/v1/conversation/{conversation_id}")
    async def conversation(conversation_id: str) -> dict[str, object]:
        state = app.state.engine.conversation_state(conversation_id)
        return _success(
            {
                "conversation_id": state.conversation_id,
                "attempt_count": state.attempt_count,
                "max_attempts": state.max_attempts,
                "excluded_providers": state.excluded_providers,
            }
        )

    @app.get("/api/v1/providers")
    async def providers(http_request: Request) -> dict[str, object]:
        data: dict[str, object] = {"providers": app.state.engine.provider_summaries()}
        if _developer_mode(http_request):
            data["diagnostics"] = _provider_diagnostics(runtime_settings)
        return _success(data)

    @app.get("/api/v1/health")
    async def health() -> dict[str, object]:
        return _success({"status": "healthy"})

    @app.get("/api/v1/router/health")
    async def router_health(http_request: Request) -> dict[str, object]:
        data: dict[str, object] = await app.state.engine.router_health()
        if _developer_mode(http_request):
            data["diagnostics"] = _router_diagnostics(runtime_settings)
        return _success(data)

    @app.get("/api/v1/providers/health")
    async def providers_health() -> dict[str, object]:
        return _success(await app.state.engine.provider_health())

    return app


app = create_app()

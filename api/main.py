"""Versioned FastAPI entry point with no routing decisions of its own."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from config.settings import Settings, get_settings
from gateway.response_gateway import ResponseGateway
from providers.dispatcher import ProviderDispatcher
from registry.model_registry import ModelRegistry
from routers.default_router import DeterministicDefaultRouter
from routers.http_router import FallbackRouter, PrimaryRouter
from routing.engine import ProviderExecutionError, RoutingEngine, RoutingEngineError
from schemas.models import ChatRequest, ErrorEnvelope, RoutingResponse, SuccessEnvelope
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
        primary_router=PrimaryRouter(settings.primary_router_url, settings.primary_router_model, settings.request_timeout),
        fallback_router=FallbackRouter(
            settings.fallback_router_url, settings.fallback_router_model, settings.request_timeout
        ),
        default_router=DeterministicDefaultRouter(registry),
        validator=DecisionValidator(registry),
        dispatcher=ProviderDispatcher(registry, settings.request_timeout),
        gateway=ResponseGateway(identity_terms),
        max_attempts=settings.max_retries,
    )


def _success(data: dict[str, object]) -> dict[str, object]:
    return SuccessEnvelope(data=data).model_dump()


def _error(code: str, message: str) -> dict[str, object]:
    return ErrorEnvelope(error={"code": code, "message": message}).model_dump()


def create_app(engine: RoutingEngine | None = None, settings: Settings | None = None) -> FastAPI:
    configure_logging()
    runtime_settings = settings or get_settings()
    routing_engine = engine or build_engine(runtime_settings)
    app = FastAPI(title="Provider-Agnostic LLM Routing Engine", version="0.1.0")
    app.state.engine = routing_engine

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content=_error("INVALID_REQUEST", "Request validation failed."))

    @app.exception_handler(ConversationNotFoundError)
    async def conversation_handler(_: Request, __: ConversationNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content=_error("INVALID_CONVERSATION", "Conversation not found."))

    @app.exception_handler(ProviderExecutionError)
    async def provider_handler(_: Request, __: ProviderExecutionError) -> JSONResponse:
        return JSONResponse(status_code=503, content=_error("PROVIDER_UNAVAILABLE", "Selected provider is unavailable."))

    @app.exception_handler(RoutingEngineError)
    async def routing_handler(_: Request, __: RoutingEngineError) -> JSONResponse:
        return JSONResponse(status_code=503, content=_error("ROUTER_FAILURE", "Unable to process request."))

    @app.post("/api/v1/chat")
    async def chat(request: ChatRequest) -> dict[str, object]:
        result: RoutingResponse = await app.state.engine.handle(request)
        return _success(result.model_dump())

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
    async def providers() -> dict[str, object]:
        return _success({"providers": app.state.engine.provider_summaries()})

    @app.get("/api/v1/health")
    async def health() -> dict[str, object]:
        return _success({"status": "healthy"})

    @app.get("/api/v1/router/health")
    async def router_health() -> dict[str, object]:
        return _success(await app.state.engine.router_health())

    @app.get("/api/v1/providers/health")
    async def providers_health() -> dict[str, object]:
        return _success(await app.state.engine.provider_health())

    return app


app = create_app()

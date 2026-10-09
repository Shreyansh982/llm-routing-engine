"""Resolve validated provider IDs into concrete adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from core.interfaces import BaseProvider
from providers.adapter import OpenAICompatibleAdapter, ProviderUnavailableError
from providers.circuit_breaker import BackendCircuits
from providers.model_catalog import ModelVerificationTarget, StartupModelVerifier
from registry.model_registry import ModelRegistry, UnknownProviderError
from schemas.models import FailureScope, ProviderRequest, ProviderResponse


class ProviderDisabledError(RuntimeError):
    failure_stage = "dispatcher"
    failure_reason = "UNKNOWN"
    http_status = None


ProviderFactory = Callable[..., BaseProvider]


class ProviderDispatcher:
    def __init__(
        self,
        registry: ModelRegistry,
        circuits: BackendCircuits,
        adapter_factory: ProviderFactory | None = None,
    ) -> None:
        self._registry = registry
        self._circuits = circuits
        self._adapter_factory = adapter_factory
        self._backend_factories: dict[str, ProviderFactory] = {
            backend: OpenAICompatibleAdapter for backend in BackendCircuits.BACKENDS
        }

    def resolve(self, provider_id: str) -> BaseProvider:
        try:
            config = self._registry.get_provider(provider_id)
        except UnknownProviderError as exc:
            raise ProviderUnavailableError(
                "Selected provider is not registered", failure_stage="dispatcher"
            ) from exc
        if not config.enabled:
            raise ProviderDisabledError("Selected provider is disabled")
        factory = self._adapter_factory or self._backend_factories.get(config.backend)
        if factory is None:
            raise ProviderUnavailableError(
                "Selected provider backend is not supported", failure_stage="dispatcher"
            )
        return factory(config, config.timeout)

    async def dispatch(self, provider_id: str, request: ProviderRequest) -> ProviderResponse:
        config = self._registry.get_provider(provider_id)
        if not self._circuits.allow_request(config.backend):
            raise ProviderUnavailableError(
                "Selected provider backend circuit is open",
                failure_reason="CIRCUIT_OPEN",
                failure_scope=FailureScope.BACKEND_TRANSIENT,
            )
        try:
            try:
                async with asyncio.timeout(config.timeout):
                    response = await self.resolve(provider_id).generate(request)
            except TimeoutError as exc:
                raise ProviderUnavailableError(
                    "Provider execution timed out", failure_reason="TIMEOUT",
                    failure_scope=FailureScope.BACKEND_TRANSIENT,
                ) from exc
        except ProviderUnavailableError as exc:
            self._circuits.record_failure(
                config.backend, exc.failure_scope == FailureScope.BACKEND_TRANSIENT
            )
            raise
        except BaseException:
            self._circuits.release_probe(config.backend)
            raise
        self._circuits.record_success(config.backend)
        return response

    async def health(self) -> dict[str, str]:
        if self._adapter_factory is None:
            providers = self._registry.list_providers()
            verifier = StartupModelVerifier([
                ModelVerificationTarget(
                    name=provider.id, backend=provider.backend, endpoint=provider.endpoint,
                    model=provider.model, api_key=provider.api_key, timeout=provider.timeout,
                ) for provider in providers if provider.enabled
            ])
            statuses = {item.name: "healthy" if item.available else "unhealthy"
                        for item in await verifier.verify()}
            return {provider.id: statuses.get(provider.id, "offline") for provider in providers}
        return {
            provider.id: await self.resolve(provider.id).health() if provider.enabled else "offline"
            for provider in self._registry.list_providers()
        }

    def circuit_snapshot(self) -> dict[str, dict[str, object]]:
        return self._circuits.snapshot()

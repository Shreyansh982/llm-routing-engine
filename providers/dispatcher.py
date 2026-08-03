"""Resolve validated provider IDs into concrete adapters."""

from __future__ import annotations

from collections.abc import Callable

from core.interfaces import BaseProvider
from providers.adapter import HTTPProviderAdapter, ProviderUnavailableError
from registry.model_registry import ModelRegistry, UnknownProviderError
from schemas.models import ProviderResponse


class ProviderDisabledError(RuntimeError):
    pass


ProviderFactory = Callable[..., BaseProvider]


class ProviderDispatcher:
    def __init__(
        self,
        registry: ModelRegistry,
        timeout: float,
        adapter_factory: ProviderFactory = HTTPProviderAdapter,
    ) -> None:
        self._registry = registry
        self._timeout = timeout
        self._adapter_factory = adapter_factory

    def resolve(self, provider_id: str) -> BaseProvider:
        try:
            config = self._registry.get_provider(provider_id)
        except UnknownProviderError as exc:
            raise ProviderUnavailableError("Selected provider is not registered") from exc
        if not config.enabled:
            raise ProviderDisabledError("Selected provider is disabled")
        return self._adapter_factory(config, self._timeout)

    async def dispatch(self, provider_id: str, prompt: str) -> ProviderResponse:
        return await self.resolve(provider_id).generate(prompt)

    async def health(self) -> dict[str, str]:
        return {
            provider.id: await self.resolve(provider.id).health() if provider.enabled else "offline"
            for provider in self._registry.list_providers()
        }

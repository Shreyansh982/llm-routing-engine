"""Single owner of provider metadata and provider ID mappings."""

from __future__ import annotations

from schemas.models import AvailableProvider, ProviderConfig


class UnknownProviderError(KeyError):
    pass


class ModelRegistry:
    def __init__(self, providers: list[ProviderConfig] | None = None) -> None:
        self._providers: dict[str, ProviderConfig] = {}
        self._order: list[str] = []
        for provider in providers or []:
            self.register(provider)

    def register(self, provider: ProviderConfig) -> None:
        if provider.id not in self._providers:
            self._order.append(provider.id)
        self._providers[provider.id] = provider

    def remove(self, provider_id: str) -> None:
        self.get_provider(provider_id)
        del self._providers[provider_id]
        self._order.remove(provider_id)

    def get_provider(self, provider_id: str) -> ProviderConfig:
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise UnknownProviderError(provider_id) from exc

    def list_providers(self) -> list[ProviderConfig]:
        return [self._providers[provider_id] for provider_id in self._order]

    def is_enabled(self, provider_id: str) -> bool:
        return self.get_provider(provider_id).enabled

    def enable(self, provider_id: str) -> None:
        self._providers[provider_id] = self.get_provider(provider_id).model_copy(update={"enabled": True})

    def disable(self, provider_id: str) -> None:
        self._providers[provider_id] = self.get_provider(provider_id).model_copy(update={"enabled": False})

    def available_for_router(self, excluded: list[str]) -> list[AvailableProvider]:
        return [
            AvailableProvider(id=provider.id, capabilities=provider.capabilities)
            for provider in self.list_providers()
            if provider.enabled and provider.id not in excluded
        ]

    def enabled_provider_ids(self) -> set[str]:
        return {provider.id for provider in self.list_providers() if provider.enabled}

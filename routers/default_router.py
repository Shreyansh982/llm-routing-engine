"""The non-AI terminal routing tier."""

from __future__ import annotations

from registry.model_registry import ModelRegistry
from schemas.models import RouterAction, RouterDecision


class DeterministicDefaultRouter:
    def __init__(self, registry: ModelRegistry) -> None:
        self._registry = registry

    def select_default(self, excluded_providers: list[str]) -> RouterDecision | None:
        for provider in self._registry.list_providers():
            if provider.enabled and provider.id not in excluded_providers:
                return RouterDecision(
                    action=RouterAction.ANSWER,
                    selected_provider=provider.id,
                    confidence=1.0,
                    reason="Deterministic terminal fallback selected the first eligible provider.",
                )
        return None

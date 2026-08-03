"""Deterministic validation of schema-conformant router decisions."""

from __future__ import annotations

from pydantic import ValidationError

from registry.model_registry import ModelRegistry, UnknownProviderError
from schemas.models import RouterAction, RouterDecision


class InvalidDecisionError(ValueError):
    pass


class DecisionValidator:
    def __init__(self, registry: ModelRegistry) -> None:
        self._registry = registry

    def validate(self, decision: RouterDecision | dict[str, object], excluded_providers: list[str]) -> RouterDecision:
        try:
            parsed = (
                decision if isinstance(decision, RouterDecision) else RouterDecision.model_validate(decision)
            )
        except ValidationError as exc:
            raise InvalidDecisionError("Router decision does not match the required schema") from exc

        if parsed.selected_provider:
            try:
                enabled = self._registry.is_enabled(parsed.selected_provider)
            except UnknownProviderError as exc:
                raise InvalidDecisionError("Router selected an unknown provider") from exc
            if not enabled:
                raise InvalidDecisionError("Router selected a disabled provider")
            if parsed.selected_provider in excluded_providers:
                raise InvalidDecisionError("Router selected an excluded provider")
        return parsed

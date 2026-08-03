from __future__ import annotations

import pytest

from registry.model_registry import ModelRegistry, UnknownProviderError
from state.manager import ConversationNotFoundError, ConversationStateManager
from tests.conftest import decision, provider
from validation.decision_validator import DecisionValidator, InvalidDecisionError


def test_state_create_update_retry_and_reset() -> None:
    manager = ConversationStateManager()
    state = manager.create("c1", "original", 3)
    manager.increment_attempt(state)
    manager.exclude_provider(state, "provider_a")
    state.previous_response = "previous"
    manager.update(state)
    loaded = manager.load("c1")
    assert (loaded.attempt_count, loaded.excluded_providers, loaded.previous_response) == (1, ["provider_a"], "previous")
    reset = manager.reset("c1")
    assert reset.attempt_count == 0 and reset.excluded_providers == [] and reset.previous_response is None


def test_unknown_conversation_and_provider_raise_controlled_errors() -> None:
    with pytest.raises(ConversationNotFoundError):
        ConversationStateManager().load("missing")
    with pytest.raises(UnknownProviderError):
        ModelRegistry().get_provider("missing")


def test_registry_preserves_order_and_excludes_disabled_metadata_from_router() -> None:
    registry = ModelRegistry([provider("provider_a", ["coding"]), provider("provider_b", ["creative_writing"], enabled=False)])
    assert [item.id for item in registry.available_for_router([])] == ["provider_a"]
    registry.disable("provider_a")
    assert registry.available_for_router([]) == []
    registry.enable("provider_b")
    assert [item.id for item in registry.available_for_router([])] == ["provider_b"]


def test_validator_rejects_unknown_disabled_excluded_and_invalid_schema() -> None:
    registry = ModelRegistry([provider("provider_a", ["coding"]), provider("provider_b", ["creative_writing"], enabled=False)])
    validator = DecisionValidator(registry)
    assert validator.validate(decision("provider_a"), []) == decision("provider_a")
    for raw, excluded in [
        ({"action": "ANSWER", "selected_provider": "missing", "confidence": 0.9, "reason": "x"}, []),
        (decision("provider_b"), []),
        (decision("provider_a"), ["provider_a"]),
        ({"action": "BAD", "confidence": 0.1, "reason": "x"}, []),
    ]:
        with pytest.raises(InvalidDecisionError):
            validator.validate(raw, excluded)

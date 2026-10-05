from __future__ import annotations

import pytest

from registry.model_registry import ModelRegistry, UnknownProviderError
from state.manager import ConversationNotFoundError, ConversationStateManager
from tests.conftest import decision, provider
from schemas.models import TaskGroup
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


def test_registry_candidate_order_uses_priority_unless_v2_policy_order_is_explicit() -> None:
    registry = ModelRegistry(
        [
            provider("second", ["general"], priority=2),
            provider("first", ["general"], priority=1),
        ]
    )
    default = registry.candidates_for(
        "general",
        requires_image=False,
        excluded_provider_ids=[],
        allowed_provider_ids=["second", "first"],
    )
    policy = registry.candidates_for(
        "general",
        requires_image=False,
        excluded_provider_ids=[],
        allowed_provider_ids=["second", "first"],
        preserve_allowed_order=True,
    )
    assert [item.id for item in default] == ["first", "second"]
    assert [item.id for item in policy] == ["second", "first"]


def test_validator_rejects_unavailable_group_image_mismatch_and_invalid_schema() -> None:
    validator = DecisionValidator()
    assert validator.validate(decision(), [TaskGroup.GENERAL], False) == decision()
    invalid = [
        ({"action": "ANSWER", "task_group": "creative", "confidence": 0.9, "reason": "x"}, [TaskGroup.GENERAL], False),
        (decision(TaskGroup.GENERAL), [TaskGroup.GENERAL, TaskGroup.MULTIMODAL], True),
        ({"action": "BAD", "confidence": 0.1, "reason": "x"}, [TaskGroup.GENERAL], False),
    ]
    for raw, groups, has_image in invalid:
        with pytest.raises(InvalidDecisionError):
            validator.validate(raw, groups, has_image)

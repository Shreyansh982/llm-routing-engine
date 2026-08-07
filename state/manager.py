"""Conversation routing/retry bookkeeping."""

from __future__ import annotations

from datetime import datetime, timezone

from schemas.models import ConversationState


class ConversationNotFoundError(KeyError):
    pass


class ConversationStateManager:
    """A deliberately in-memory state store; persistent storage is roadmap scope."""

    def __init__(self) -> None:
        self._conversations: dict[tuple[str, str], ConversationState] = {}

    def create(
        self, conversation_id: str, original_query: str, max_attempts: int, tenant_id: str = "legacy"
    ) -> ConversationState:
        state = ConversationState(
            conversation_id=conversation_id,
            original_query=original_query,
            max_attempts=max_attempts,
        )
        self._conversations[(tenant_id, conversation_id)] = state
        return state.model_copy(deep=True)

    def load(self, conversation_id: str, tenant_id: str = "legacy") -> ConversationState:
        try:
            return self._conversations[(tenant_id, conversation_id)].model_copy(deep=True)
        except KeyError as exc:
            raise ConversationNotFoundError(conversation_id) from exc

    def update(self, state: ConversationState, tenant_id: str = "legacy") -> ConversationState:
        key = (tenant_id, state.conversation_id)
        if key not in self._conversations:
            raise ConversationNotFoundError(state.conversation_id)
        state.updated_at = datetime.now(timezone.utc)
        self._conversations[key] = state.model_copy(deep=True)
        return state.model_copy(deep=True)

    def increment_attempt(self, state: ConversationState, tenant_id: str = "legacy") -> ConversationState:
        state.attempt_count += 1
        return self.update(state, tenant_id)

    def exclude_provider(
        self, state: ConversationState, provider_id: str, tenant_id: str = "legacy"
    ) -> ConversationState:
        if provider_id not in state.excluded_providers:
            state.excluded_providers.append(provider_id)
        return self.update(state, tenant_id)

    def reset(self, conversation_id: str, tenant_id: str = "legacy") -> ConversationState:
        state = self.load(conversation_id, tenant_id)
        state.previous_response = None
        state.attempt_count = 0
        state.excluded_providers = []
        state.last_provider = None
        return self.update(state, tenant_id)

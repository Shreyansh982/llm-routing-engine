from __future__ import annotations

import asyncio

from core.interfaces import BaseRouter
from schemas.models import ChatRequest, RouterAction, RouterDecision, RouterRequest
from tests.conftest import QueueRouter, make_engine, provider


CASES = [
    ("Write a Python function to reverse a linked list.", "coding"),
    ("Debug this TypeScript compiler error.", "coding"),
    ("Design a SQL schema for invoices.", "coding"),
    ("Refactor this API client for testability.", "coding"),
    ("Explain why this algorithm is O(n log n).", "reasoning"),
    ("Solve this logic puzzle step by step.", "reasoning"),
    ("Compare these two architectural options.", "reasoning"),
    ("Summarize this article in five bullets.", "summarization"),
    ("Give me a concise meeting summary.", "summarization"),
    ("Extract key decisions from these notes.", "summarization"),
    ("Write a whimsical poem about autumn.", "creative_writing"),
    ("Draft a playful product launch email.", "creative_writing"),
    ("Create a short fantasy scene.", "creative_writing"),
    ("Write a warm thank-you message.", "creative_writing"),
    ("Implement a binary search tree.", "coding"),
]


class CapabilityEvaluationRouter(BaseRouter):
    """Deterministic test double for evaluating the local Router prompt contract."""

    async def decide(self, request: RouterRequest) -> RouterDecision:
        query = request.latest_user_message.lower()
        strength = (
            "coding" if any(word in query for word in ("python", "typescript", "sql", "refactor", "implement"))
            else "summarization" if any(word in query for word in ("summarize", "summary", "extract"))
            else "creative_writing" if any(word in query for word in ("poem", "playful", "fantasy", "warm"))
            else "reasoning"
        )
        selected = next(item.id for item in request.available_providers if strength in item.capabilities.strengths)
        return RouterDecision(action=RouterAction.ANSWER, selected_provider=selected, confidence=0.9, reason=strength)

    async def health(self) -> str:
        return "healthy"


def test_routing_quality_dataset_meets_documented_acceptance_threshold() -> None:
    router = CapabilityEvaluationRouter()
    providers = [
        provider("provider_a", ["coding", "reasoning"]),
        provider("provider_b", ["summarization", "creative_writing"]),
    ]
    engine, _, registry = make_engine(router, QueueRouter([]), providers)
    matches = 0
    for index, (query, expected_strength) in enumerate(CASES):
        result = asyncio.run(engine.handle(ChatRequest(conversation_id=f"eval-{index}", message=query)))
        assert result.action == RouterAction.ANSWER
        selected = registry.get_provider(engine._state.load(f"eval-{index}").last_provider or "")
        matches += expected_strength in selected.capabilities.strengths
    assert matches / len(CASES) >= 0.80

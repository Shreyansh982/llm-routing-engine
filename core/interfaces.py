"""Dependency-inverted interfaces used by the routing orchestration."""

from __future__ import annotations

from abc import ABC, abstractmethod

from schemas.models import ProviderResponse, RouterDecision, RouterRequest


class BaseRouter(ABC):
    @abstractmethod
    async def decide(self, request: RouterRequest) -> RouterDecision:
        """Return a structured routing decision."""

    @abstractmethod
    async def health(self) -> str:
        """Return ``healthy``, ``degraded``, or ``unhealthy``."""


class BaseProvider(ABC):
    @abstractmethod
    async def generate(self, prompt: str) -> ProviderResponse:
        """Generate a response from the supplied user prompt only."""

    @abstractmethod
    async def health(self) -> str:
        """Return provider availability without leaking implementation details."""

"""Small process-local circuit breakers shared by routers and task providers."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class BackendCircuit:
    failure_threshold: int
    open_seconds: float
    half_open_probes: int
    clock: Callable[[], float] = time.monotonic
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at: float | None = None
    half_open_inflight: int = 0

    def allow_request(self) -> bool:
        if self.state == CircuitState.OPEN:
            assert self.opened_at is not None
            if self.clock() - self.opened_at < self.open_seconds:
                return False
            self.state = CircuitState.HALF_OPEN
            self.half_open_inflight = 0
        if self.state == CircuitState.HALF_OPEN:
            if self.half_open_inflight >= self.half_open_probes:
                return False
            self.half_open_inflight += 1
        return True

    def record_success(self) -> None:
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.opened_at = None
        self.half_open_inflight = 0

    def record_failure(self, backend_transient: bool) -> None:
        if not backend_transient:
            if self.state == CircuitState.HALF_OPEN:
                self.record_success()
            return
        if self.state == CircuitState.HALF_OPEN:
            self._open()
            return
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold:
            self._open()

    def snapshot(self) -> dict[str, object]:
        state = self.state
        if (
            state == CircuitState.OPEN
            and self.opened_at is not None
            and self.clock() - self.opened_at >= self.open_seconds
        ):
            state = CircuitState.HALF_OPEN
        return {
            "state": state.value,
            "consecutive_failures": self.consecutive_failures,
            "half_open_inflight": self.half_open_inflight,
        }

    def _open(self) -> None:
        self.state = CircuitState.OPEN
        self.opened_at = self.clock()
        self.half_open_inflight = 0


class BackendCircuits:
    BACKENDS = ("google", "nvidia", "openrouter", "groq")

    def __init__(
        self,
        failure_threshold: int = 3,
        open_seconds: float = 30,
        half_open_probes: int = 1,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._circuits = {
            backend: BackendCircuit(
                failure_threshold=failure_threshold,
                open_seconds=open_seconds,
                half_open_probes=half_open_probes,
                clock=clock,
            )
            for backend in self.BACKENDS
        }

    def allow_request(self, backend: str) -> bool:
        return self._circuits[backend].allow_request()

    def record_success(self, backend: str) -> None:
        self._circuits[backend].record_success()

    def release_probe(self, backend: str) -> None:
        circuit = self._circuits[backend]
        if circuit.state == CircuitState.HALF_OPEN:
            circuit.half_open_inflight = max(0, circuit.half_open_inflight - 1)

    def record_failure(self, backend: str, backend_transient: bool) -> None:
        self._circuits[backend].record_failure(backend_transient)

    def snapshot(self) -> dict[str, dict[str, object]]:
        return {backend: circuit.snapshot() for backend, circuit in self._circuits.items()}

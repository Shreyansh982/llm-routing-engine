"""Provider-agnostic application contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class RouterAction(StrEnum):
    ANSWER = "ANSWER"
    RETRY = "RETRY"
    CLARIFY = "CLARIFY"
    STOP = "STOP"


class FailureStage(StrEnum):
    """The architecture boundary at which a request terminally failed."""

    NONE = "none"
    PRIMARY_ROUTER = "primary_router"
    FALLBACK_ROUTER = "fallback_router"
    DISPATCHER = "dispatcher"
    PROVIDER = "provider"
    GATEWAY = "gateway"
    VALIDATOR = "validator"
    DEFAULT_ROUTER = "default_router"


def http_failure_reason(status_code: int) -> str:
    """Return a stable, JSON-safe failure reason for an upstream HTTP response."""
    phrases = {
        402: "PAYMENT_REQUIRED",
        404: "MODEL_NOT_FOUND",
        429: "RATE_LIMIT",
        500: "INTERNAL_SERVER_ERROR",
        503: "PROVIDER_UNAVAILABLE",
    }
    return f"HTTP_{status_code}_{phrases.get(status_code, 'ERROR')}"


class ProviderCapability(BaseModel):
    strengths: list[str] = Field(min_length=1)
    speed_tier: str = Field(min_length=1)
    context_size: str = Field(min_length=1)


class ProviderConfig(BaseModel):
    """Private registry metadata; it is never included in a RouterRequest."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    enabled: bool = True
    backend: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key: SecretStr | None = None
    timeout: float = Field(gt=0)
    capabilities: ProviderCapability
    identity_terms: list[str] = Field(default_factory=list)


class AvailableProvider(BaseModel):
    """The intentionally vendor-neutral view provided to the Router LLM."""

    id: str
    capabilities: ProviderCapability


class RouterRequest(BaseModel):
    original_query: str = Field(min_length=1)
    latest_user_message: str = Field(min_length=1)
    previous_response: str | None = None
    attempt: int = Field(ge=0)
    max_attempts: int = Field(ge=0)
    excluded_providers: list[str] = Field(default_factory=list)
    available_providers: list[AvailableProvider] = Field(default_factory=list)


class RouterDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: RouterAction
    selected_provider: str | None = None
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def answer_requires_provider(self) -> "RouterDecision":
        if self.action == RouterAction.ANSWER and not self.selected_provider:
            raise ValueError("ANSWER decisions require selected_provider")
        return self


class ProviderRequest(BaseModel):
    prompt: str = Field(min_length=1)


class ProviderResponse(BaseModel):
    response: str


class ConversationState(BaseModel):
    conversation_id: str = Field(min_length=1)
    original_query: str = Field(min_length=1)
    previous_response: str | None = None
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(ge=0)
    excluded_providers: list[str] = Field(default_factory=list)
    # The documents require retries to exclude the previous provider but omit where it
    # is stored. This field is the minimum routing bookkeeping needed to implement that.
    last_provider: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChatRequest(BaseModel):
    conversation_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retry: bool = False

    @field_validator("conversation_id", "message")
    @classmethod
    def not_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value


class RoutingResponse(BaseModel):
    conversation_id: str
    action: RouterAction
    response: str
    diagnostics: "RoutingDiagnostics | None" = None


class LatencyBreakdown(BaseModel):
    router_ms: float = Field(ge=0)
    provider_ms: float = Field(ge=0)
    gateway_ms: float = Field(ge=0)
    total_ms: float = Field(ge=0)


class RoutingDiagnostics(BaseModel):
    """Developer-only observability data; never emitted on a normal chat response."""

    developer_mode: bool = True
    request_id: str
    timestamp: datetime
    request_timestamp: datetime
    completed_at: datetime | None = None
    router_used: str | None = None
    selected_provider: str | None = None
    provider_id: str | None = None
    backend: str | None = None
    model: str | None = None
    provider_backend: str | None = None
    configured_model: str | None = None
    routing_reason: str | None = None
    capabilities_considered: list[AvailableProvider] = Field(default_factory=list)
    fallback_used: bool = False
    retry_count: int = Field(ge=0)
    latency_breakdown: LatencyBreakdown
    http_status: int | None = None
    # The public API may intentionally use a controlled status (for example 503).
    # This field preserves the status returned by the provider backend itself.
    upstream_http_status: int | None = None
    failure_stage: FailureStage = FailureStage.NONE
    # A completed answer has no failure. UNKNOWN is reserved for an actual failure
    # whose cause could not be classified.
    failure_reason: str = "NONE"
    failure_level: str | None = None
    provider_error: str | None = None


class SuccessEnvelope(BaseModel):
    success: bool = True
    data: dict[str, Any]


class ErrorBody(BaseModel):
    code: str
    message: str
    failure_level: str | None = None


class ErrorEnvelope(BaseModel):
    success: bool = False
    error: ErrorBody

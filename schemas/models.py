"""Provider-agnostic application contracts."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class RouterAction(StrEnum):
    ANSWER = "ANSWER"
    RETRY = "RETRY"
    CLARIFY = "CLARIFY"
    STOP = "STOP"


class TaskGroup(StrEnum):
    GENERAL = "general"
    CODING_REASONING = "coding_reasoning"
    CREATIVE = "creative"
    MULTIMODAL = "multimodal"


class InputModality(StrEnum):
    TEXT = "text"
    IMAGE = "image"


class FailureScope(StrEnum):
    BACKEND_TRANSIENT = "backend_transient"
    MODEL_SPECIFIC = "model_specific"
    NON_TRANSIENT = "non_transient"


class FailureStage(StrEnum):
    """The architecture boundary at which a request terminally failed."""

    NONE = "none"
    REQUEST = "request"
    DEADLINE = "deadline"
    PRIMARY_ROUTER = "primary_router"
    FALLBACK_ROUTER = "fallback_router"
    DISPATCHER = "dispatcher"
    PROVIDER = "provider"
    GATEWAY = "gateway"
    VALIDATOR = "validator"
    GOVERNANCE = "governance"


def http_failure_reason(status_code: int) -> str:
    """Return a stable, JSON-safe failure reason for an upstream HTTP response."""
    phrases = {
        400: "BAD_REQUEST",
        401: "AUTHENTICATION_FAILED",
        402: "PAYMENT_REQUIRED",
        403: "PERMISSION_DENIED",
        404: "MODEL_NOT_FOUND",
        429: "RATE_LIMIT",
        500: "INTERNAL_SERVER_ERROR",
        502: "BAD_GATEWAY",
        503: "PROVIDER_UNAVAILABLE",
        504: "GATEWAY_TIMEOUT",
    }
    return f"HTTP_{status_code}_{phrases.get(status_code, 'ERROR')}"


def failure_class(error: Exception) -> str:
    status = getattr(error, "http_status", None)
    if status in {401, 403}:
        return "AUTH"
    if status == 429:
        return "HTTP_429"
    if status is not None and status >= 500:
        return "HTTP_5XX"
    if status == 404 or getattr(error, "failure_scope", None) == FailureScope.MODEL_SPECIFIC:
        return "MODEL_UNAVAILABLE"
    if status is not None and status >= 400:
        return "HTTP_4XX"
    return getattr(error, "failure_reason", "UNKNOWN")


class ProviderCapability(BaseModel):
    strengths: list[str] = Field(min_length=1)
    speed_tier: str = Field(min_length=1)
    context_size: str = Field(min_length=1)
    input_modalities: list[InputModality] = Field(default_factory=lambda: [InputModality.TEXT])
    output_modalities: list[str] = Field(default_factory=lambda: ["text"])
    supports_tools: bool = False
    supports_structured_output: bool = False


class ProviderConfig(BaseModel):
    """Private registry metadata; it is never included in a RouterRequest."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    enabled: bool = True
    backend: Literal["google", "nvidia", "openrouter", "groq"]
    endpoint: str = Field(min_length=1)
    model: str = Field(min_length=1)
    task_group: TaskGroup
    priority: int = Field(ge=1)
    api_key: SecretStr | None = None
    timeout: float = Field(gt=0)
    capabilities: ProviderCapability
    identity_terms: list[str] = Field(default_factory=list)
    # Operational metadata remains private to the Registry and control plane.  The
    # Router continues to receive only ``AvailableProvider`` capability descriptors.
    regions: list[str] = Field(default_factory=list)
    supported_data_classifications: list[str] = Field(
        default_factory=lambda: ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    )
    cost_per_1k_tokens: float = Field(default=1.0, ge=0)
    latency_ms: float = Field(default=1000.0, gt=0)
    quality_score: float = Field(default=0.5, ge=0, le=1)
    health_score: float = Field(default=1.0, ge=0, le=1)
    capacity_weight: float = Field(default=1.0, gt=0)


class AvailableProvider(BaseModel):
    """The intentionally vendor-neutral view provided to the Router LLM."""

    id: str
    capabilities: ProviderCapability


class RouterRequest(BaseModel):
    retry: bool = False
    original_query: str = Field(min_length=1)
    latest_user_message: str = Field(min_length=1)
    previous_response: str | None = None
    attempt: int = Field(ge=0)
    max_attempts: int = Field(ge=0)
    has_image: bool = False
    available_task_groups: list[TaskGroup] = Field(default_factory=list)


class RouterDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: RouterAction
    task_group: TaskGroup | None = None
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def answer_requires_task_group(self) -> "RouterDecision":
        if self.action == RouterAction.ANSWER and self.task_group is None:
            raise ValueError("ANSWER decisions require task_group")
        if self.action != RouterAction.ANSWER and self.task_group is not None:
            raise ValueError("Only ANSWER decisions may include task_group")
        return self


class ImageInput(BaseModel):
    media_type: Literal["image/jpeg", "image/png", "image/webp"]
    data_base64: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def valid_image(cls, value: Any) -> Any:
        from config.settings import get_settings

        if not isinstance(value, dict) or not isinstance(value.get("data_base64"), str):
            return value
        encoded = value["data_base64"]
        media_type = value.get("media_type")
        limit = get_settings().max_image_bytes
        if len(encoded) > 4 * ((limit + 2) // 3):
            raise ValueError("image exceeds the configured size limit")
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("must contain valid base64") from exc
        if len(decoded) > limit:
            raise ValueError("image exceeds the configured size limit")
        valid = (
            media_type == "image/png" and decoded.startswith(b"\x89PNG\r\n\x1a\n")
            and len(decoded) >= 33 and decoded[12:16] == b"IHDR"
            or media_type == "image/jpeg" and decoded.startswith(b"\xff\xd8\xff")
            and decoded.endswith(b"\xff\xd9")
            or media_type == "image/webp" and len(decoded) >= 20
            and decoded[:4] == b"RIFF" and decoded[8:12] == b"WEBP"
            and decoded[12:16] in {b"VP8 ", b"VP8L", b"VP8X"}
        )
        if not valid:
            raise ValueError("image content does not match its media type")
        return value


class ProviderRequest(BaseModel):
    text: str = Field(min_length=1)
    image: ImageInput | None = None


class ProviderResponse(BaseModel):
    response: str


class ConversationState(BaseModel):
    has_image: bool = False
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
    image: ImageInput | None = None
    retry: bool = False

    @field_validator("conversation_id", "message")
    @classmethod
    def not_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value


class TenantContext(BaseModel):
    """Tenant identity resolved at the API boundary for additive V2 requests."""

    tenant_id: str = Field(default="default", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


class RoutingContext(BaseModel):
    """Explicit, deterministic control-plane input; it never contains provider mappings."""

    policy_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]*$")
    data_classification: str = Field(default="INTERNAL", min_length=1, max_length=64)
    region: str | None = Field(default=None, min_length=1, max_length=64)
    workload_class: str | None = Field(default=None, min_length=1, max_length=64)


class V2ChatRequest(ChatRequest):
    """Additive V2 chat contract.  The V1 request model remains untouched."""

    routing_context: RoutingContext = Field(default_factory=RoutingContext)


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
    task_group: TaskGroup | None = None
    router_attempts: list[dict[str, Any]] = Field(default_factory=list)
    initial_provider: str | None = None
    final_provider: str | None = None
    fallback_path: list[dict[str, Any]] = Field(default_factory=list)
    fallback_count: int = Field(default=0, ge=0)
    circuit_state: dict[str, dict[str, Any]] = Field(default_factory=dict)
    capabilities_considered: list[AvailableProvider] = Field(default_factory=list)
    fallback_used: bool = False
    router_fallback_used: bool = False
    provider_fallback_used: bool = False
    retry_count: int = Field(ge=0)
    latency_breakdown: LatencyBreakdown
    http_status: int | None = None
    # The public API may intentionally use a controlled status (for example 503).
    # This field preserves the status returned by the provider backend itself.
    upstream_http_status: int | None = None
    upstream_status: int | None = None
    failure_class: str | None = None
    failure_stage: FailureStage = FailureStage.NONE
    # A completed answer has no failure. UNKNOWN is reserved for an actual failure
    # whose cause could not be classified.
    failure_reason: str = "NONE"
    failure_level: str | None = None
    provider_error: str | None = None
    # V2.5 policy references are safe developer/audit metadata. They identify immutable
    # control-plane versions, never endpoints, credentials, or concrete model mappings.
    tenant_id: str | None = None
    policy_id: str | None = None
    policy_version: int | None = None
    governance_policy_id: str | None = None
    governance_policy_version: int | None = None
    policy_candidate_ids: list[str] | None = None
    governance_exclusions: list[dict[str, str]] | None = None


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

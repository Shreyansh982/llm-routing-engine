"""Provider-agnostic application contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RouterAction(StrEnum):
    ANSWER = "ANSWER"
    RETRY = "RETRY"
    CLARIFY = "CLARIFY"
    STOP = "STOP"


class ProviderCapability(BaseModel):
    strengths: list[str] = Field(min_length=1)
    speed_tier: str = Field(min_length=1)
    context_size: str = Field(min_length=1)


class ProviderConfig(BaseModel):
    """Private registry metadata; it is never included in a RouterRequest."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    enabled: bool = True
    endpoint: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key: str | None = None
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


class SuccessEnvelope(BaseModel):
    success: bool = True
    data: dict[str, Any]


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorEnvelope(BaseModel):
    success: bool = False
    error: ErrorBody

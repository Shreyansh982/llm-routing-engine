"""Deterministic control-plane contracts.  These models are deliberately provider agnostic."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schemas.models import AvailableProvider, RoutingContext, TenantContext


class PolicyKind(StrEnum):
    LEGACY_BALANCED = "legacy-balanced"
    BALANCED = "balanced"
    CHEAPEST = "cheapest"
    LOWEST_LATENCY = "lowest_latency"
    HIGHEST_QUALITY = "highest_quality"
    COMPLIANCE = "compliance"


class PolicyDefinition(BaseModel):
    """A tenant-scoped, versioned built-in policy definition."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
    policy_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    kind: PolicyKind
    description: str = Field(default="", max_length=500)
    version: int = Field(default=1, ge=1)
    active: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class GovernancePolicy(BaseModel):
    """Hard, deterministic tenant eligibility constraints applied before the Router."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
    policy_id: str = Field(default="default-governance", pattern=r"^[a-z][a-z0-9_-]*$")
    version: int = Field(default=1, ge=1)
    allowed_provider_ids: list[str] = Field(default_factory=list)
    denied_provider_ids: list[str] = Field(default_factory=list)
    allowed_regions: list[str] = Field(default_factory=list)
    active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("allowed_provider_ids", "denied_provider_ids", "allowed_regions")
    @classmethod
    def unique_values(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("values must be unique")
        return values


class GovernanceExclusion(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_id: str
    reason: str


class CandidateRanking(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_id: str
    rank: int = Field(ge=1)
    score: float
    reason: str


class PolicyAuditReference(BaseModel):
    """Immutable per-request control-plane reference, retained without raw request content."""

    model_config = ConfigDict(frozen=True)

    record_id: str = Field(default_factory=lambda: str(uuid4()))
    request_id: str
    tenant_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    policy_id: str
    policy_version: int
    governance_policy_id: str
    governance_policy_version: int
    candidate_ids: tuple[str, ...]
    exclusions: tuple[GovernanceExclusion, ...] = ()
    rankings: tuple[CandidateRanking, ...] = ()


class CandidateSet(BaseModel):
    """The sole policy/governance output consumed by the existing routing workflow."""

    providers: list[AvailableProvider]
    policy: PolicyDefinition
    governance: GovernancePolicy
    exclusions: list[GovernanceExclusion] = Field(default_factory=list)
    rankings: list[CandidateRanking] = Field(default_factory=list)


class V2PolicyCreateRequest(BaseModel):
    policy_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    kind: PolicyKind
    description: str = Field(default="", max_length=500)
    activate: bool = False


class V2GovernancePolicyRequest(BaseModel):
    policy_id: str = Field(default="default-governance", pattern=r"^[a-z][a-z0-9_-]*$")
    allowed_provider_ids: list[str] = Field(default_factory=list)
    denied_provider_ids: list[str] = Field(default_factory=list)
    allowed_regions: list[str] = Field(default_factory=list)
    activate: bool = True


class V2PolicyEvaluationRequest(BaseModel):
    routing_context: RoutingContext = Field(default_factory=RoutingContext)
    excluded_provider_ids: list[str] = Field(default_factory=list)


class V2ChatControlPlaneContext(BaseModel):
    tenant: TenantContext
    routing: RoutingContext

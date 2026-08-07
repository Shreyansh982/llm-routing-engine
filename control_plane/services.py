"""Deterministic governance and policy evaluation; no prompt interpretation or provider execution."""

from __future__ import annotations

from control_plane.models import CandidateRanking, CandidateSet, GovernanceExclusion, GovernancePolicy, PolicyDefinition, PolicyKind
from control_plane.repository import GovernanceRepository, PolicyRepository
from registry.model_registry import ModelRegistry
from schemas.models import AvailableProvider, RoutingContext, TenantContext


class PolicyNotFoundError(KeyError):
    pass


class GovernanceEvaluator:
    def __init__(self, repository: GovernanceRepository) -> None:
        self._repository = repository

    def evaluate(
        self,
        tenant: TenantContext,
        context: RoutingContext,
        providers: list[AvailableProvider],
        registry: ModelRegistry,
    ) -> tuple[GovernancePolicy, list[AvailableProvider], list[GovernanceExclusion]]:
        policy = self._repository.get_active(tenant.tenant_id)
        allowed: list[AvailableProvider] = []
        exclusions: list[GovernanceExclusion] = []
        for provider in providers:
            config = registry.get_provider(provider.id)
            reason = self._exclusion_reason(policy, context, config.id, config.regions, config.supported_data_classifications)
            if reason:
                exclusions.append(GovernanceExclusion(provider_id=provider.id, reason=reason))
            else:
                allowed.append(provider)
        return policy, allowed, exclusions

    @staticmethod
    def _exclusion_reason(
        policy: GovernancePolicy,
        context: RoutingContext,
        provider_id: str,
        provider_regions: list[str],
        classifications: list[str],
    ) -> str | None:
        if policy.allowed_provider_ids and provider_id not in policy.allowed_provider_ids:
            return "PROVIDER_NOT_ALLOWED"
        if provider_id in policy.denied_provider_ids:
            return "PROVIDER_DENIED"
        if context.data_classification not in classifications:
            return "DATA_CLASSIFICATION_NOT_ALLOWED"
        if context.region and policy.allowed_regions and context.region not in policy.allowed_regions:
            return "TENANT_REGION_DENIED"
        if context.region and provider_regions and context.region not in provider_regions:
            return "REGION_NOT_ALLOWED"
        return None


class PolicyEvaluator:
    """Ranks eligible candidates using Registry metadata only; it never selects a provider."""

    def __init__(self, repository: PolicyRepository) -> None:
        self._repository = repository

    def evaluate(
        self, tenant: TenantContext, context: RoutingContext, providers: list[AvailableProvider], registry: ModelRegistry
    ) -> tuple[PolicyDefinition, list[AvailableProvider], list[CandidateRanking]]:
        policy = self._resolve_policy(tenant, context)
        indexed = list(enumerate(providers))
        if policy.kind in {PolicyKind.LEGACY_BALANCED, PolicyKind.COMPLIANCE}:
            ordered = indexed
        elif policy.kind == PolicyKind.BALANCED:
            ordered = sorted(indexed, key=lambda item: (-self._balanced_score(registry, item[1].id), item[1].id))
        elif policy.kind == PolicyKind.CHEAPEST:
            ordered = sorted(indexed, key=lambda item: (registry.get_provider(item[1].id).cost_per_1k_tokens, item[1].id))
        elif policy.kind == PolicyKind.LOWEST_LATENCY:
            ordered = sorted(indexed, key=lambda item: (registry.get_provider(item[1].id).latency_ms, item[1].id))
        else:  # HIGHEST_QUALITY
            ordered = sorted(indexed, key=lambda item: (-registry.get_provider(item[1].id).quality_score, item[1].id))
        ordered_providers = [provider for _, provider in ordered]
        rankings = [
            CandidateRanking(
                provider_id=provider.id,
                rank=rank,
                score=self._score(policy.kind, registry, provider.id),
                reason=self._reason(policy.kind),
            )
            for rank, provider in enumerate(ordered_providers, start=1)
        ]
        return policy, ordered_providers, rankings

    def _resolve_policy(self, tenant: TenantContext, context: RoutingContext) -> PolicyDefinition:
        if context.policy_id:
            policy = self._repository.get(tenant.tenant_id, context.policy_id)
            if policy is None:
                raise PolicyNotFoundError(context.policy_id)
            return policy
        return self._repository.get_active(tenant.tenant_id)

    @staticmethod
    def _balanced_score(registry: ModelRegistry, provider_id: str) -> float:
        provider = registry.get_provider(provider_id)
        return provider.health_score * provider.capacity_weight

    def _score(self, kind: PolicyKind, registry: ModelRegistry, provider_id: str) -> float:
        provider = registry.get_provider(provider_id)
        if kind == PolicyKind.BALANCED:
            return self._balanced_score(registry, provider_id)
        if kind == PolicyKind.CHEAPEST:
            return -provider.cost_per_1k_tokens
        if kind == PolicyKind.LOWEST_LATENCY:
            return -provider.latency_ms
        if kind == PolicyKind.HIGHEST_QUALITY:
            return provider.quality_score
        return 0.0

    @staticmethod
    def _reason(kind: PolicyKind) -> str:
        return {
            PolicyKind.LEGACY_BALANCED: "Preserved Registry order for V1 compatibility.",
            PolicyKind.BALANCED: "Ranked by health and capacity metadata.",
            PolicyKind.CHEAPEST: "Ranked by projected cost metadata.",
            PolicyKind.LOWEST_LATENCY: "Ranked by published latency metadata.",
            PolicyKind.HIGHEST_QUALITY: "Ranked by published quality metadata.",
            PolicyKind.COMPLIANCE: "Preserved eligible Registry order after governance filtering.",
        }[kind]


class CandidateSetResolver:
    """Composes Registry, governance, and policy into the Router's existing candidate shape."""

    def __init__(self, registry: ModelRegistry, governance: GovernanceEvaluator, policies: PolicyEvaluator) -> None:
        self._registry = registry
        self._governance = governance
        self._policies = policies

    def resolve(self, tenant: TenantContext, context: RoutingContext, excluded: list[str]) -> CandidateSet:
        available = self._registry.available_for_router(excluded)
        governance, eligible, exclusions = self._governance.evaluate(tenant, context, available, self._registry)
        policy, ranked, rankings = self._policies.evaluate(tenant, context, eligible, self._registry)
        return CandidateSet(
            providers=ranked,
            policy=policy,
            governance=governance,
            exclusions=exclusions,
            rankings=rankings,
        )

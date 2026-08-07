"""Small in-memory, append-only repositories for the Phase 2.5-A POC foundation."""

from __future__ import annotations

from collections import defaultdict

from control_plane.models import GovernancePolicy, PolicyAuditReference, PolicyDefinition, PolicyKind


class PolicyRepository:
    """Stores immutable policy versions and a tenant's active policy pointer."""

    def __init__(self) -> None:
        self._versions: dict[tuple[str, str], list[PolicyDefinition]] = defaultdict(list)
        self._active: dict[str, str] = {}

    def list(self, tenant_id: str) -> list[PolicyDefinition]:
        active_id = self._active.get(tenant_id)
        return [
            versions[-1].model_copy(update={"active": policy_id == active_id})
            for (tenant, policy_id), versions in self._versions.items()
            if tenant == tenant_id
        ]

    def get_active(self, tenant_id: str) -> PolicyDefinition:
        policy_id = self._active.get(tenant_id)
        if policy_id is not None:
            return self._versions[(tenant_id, policy_id)][-1]
        return PolicyDefinition(
            tenant_id=tenant_id,
            policy_id="legacy-balanced",
            kind=PolicyKind.LEGACY_BALANCED,
            active=True,
        )

    def get(self, tenant_id: str, policy_id: str) -> PolicyDefinition | None:
        versions = self._versions.get((tenant_id, policy_id))
        return (
            versions[-1].model_copy(update={"active": self._active.get(tenant_id) == policy_id})
            if versions
            else None
        )

    def create(self, definition: PolicyDefinition, activate: bool = False) -> PolicyDefinition:
        key = (definition.tenant_id, definition.policy_id)
        versions = self._versions[key]
        created = definition.model_copy(update={"version": len(versions) + 1, "active": activate})
        versions.append(created)
        if activate:
            self.activate(created.tenant_id, created.policy_id)
        return self.get(created.tenant_id, created.policy_id) or created

    def activate(self, tenant_id: str, policy_id: str) -> PolicyDefinition:
        policy = self.get(tenant_id, policy_id)
        if policy is None:
            raise KeyError(policy_id)
        self._active[tenant_id] = policy_id
        return policy.model_copy(update={"active": True})


class GovernanceRepository:
    def __init__(self) -> None:
        self._versions: dict[str, list[GovernancePolicy]] = defaultdict(list)

    def get_active(self, tenant_id: str) -> GovernancePolicy:
        versions = self._versions.get(tenant_id)
        if versions:
            return versions[-1]
        return GovernancePolicy(tenant_id=tenant_id)

    def upsert(self, policy: GovernancePolicy) -> GovernancePolicy:
        versions = self._versions[policy.tenant_id]
        created = policy.model_copy(update={"version": len(versions) + 1, "active": True})
        versions.append(created)
        return created


class PolicyAuditRepository:
    """Append-only policy references. Broad V2 audit persistence remains out of scope."""

    def __init__(self) -> None:
        self._records: list[PolicyAuditReference] = []

    def append(self, reference: PolicyAuditReference) -> None:
        self._records.append(reference)

    def list(self, tenant_id: str) -> list[PolicyAuditReference]:
        return [record for record in self._records if record.tenant_id == tenant_id]

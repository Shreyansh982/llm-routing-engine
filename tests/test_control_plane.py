from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import create_app
from control_plane.models import PolicyDefinition, PolicyKind
from control_plane.repository import PolicyRepository
from control_plane.services import PolicyEvaluator
from schemas.models import RoutingContext, TenantContext
from tests.conftest import FakeProvider, QueueRouter, decision, make_engine, provider


def test_all_builtin_policies_rank_registry_metadata_deterministically() -> None:
    providers = [
        provider("provider_a", ["coding"]).model_copy(
            update={"cost_per_1k_tokens": 3, "latency_ms": 300, "quality_score": 0.7, "health_score": 0.5, "capacity_weight": 1}
        ),
        provider("provider_b", ["reasoning"]).model_copy(
            update={"cost_per_1k_tokens": 1, "latency_ms": 100, "quality_score": 0.9, "health_score": 0.8, "capacity_weight": 2}
        ),
    ]
    _, _, registry = make_engine(QueueRouter([]), QueueRouter([]), providers)
    repository = PolicyRepository()
    evaluator = PolicyEvaluator(repository)
    tenant = TenantContext(tenant_id="tenant-a")
    candidates = registry.available_for_router([])

    expected_first = {
        PolicyKind.LEGACY_BALANCED: "provider_a",
        PolicyKind.BALANCED: "provider_b",
        PolicyKind.CHEAPEST: "provider_b",
        PolicyKind.LOWEST_LATENCY: "provider_b",
        PolicyKind.HIGHEST_QUALITY: "provider_b",
        PolicyKind.COMPLIANCE: "provider_a",
    }
    for kind, expected in expected_first.items():
        policy_id = f"policy-{kind.value.replace('_', '-') }"
        repository.create(PolicyDefinition(tenant_id=tenant.tenant_id, policy_id=policy_id, kind=kind))
        _, ranked, rankings = evaluator.evaluate(
            tenant, RoutingContext(policy_id=policy_id), candidates, registry
        )
        assert ranked[0].id == expected
        assert rankings[0].provider_id == expected


def test_v2_policy_lifecycle_evaluation_and_audit_reference() -> None:
    engine, _, _ = make_engine(QueueRouter([decision("provider_b")]), QueueRouter([]))
    FakeProvider.responses = {"provider_b": "answer"}
    client = TestClient(create_app(engine=engine))
    headers = {"X-Tenant-ID": "tenant-a", "X-Developer-Mode": "true"}

    created = client.post(
        "/api/v2/policies",
        headers=headers,
        json={"policy_id": "low-cost", "kind": "cheapest", "activate": True},
    )
    evaluation = client.post(
        "/api/v2/policies/evaluate",
        headers=headers,
        json={"routing_context": {"policy_id": "low-cost"}},
    )
    chat = client.post(
        "/api/v2/chat",
        headers=headers,
        json={"conversation_id": "same-id", "message": "hello", "routing_context": {"policy_id": "low-cost"}},
    )
    audit = client.get("/api/v2/audit/policy-references", headers=headers)

    assert created.status_code == 200
    assert evaluation.status_code == 200
    assert chat.status_code == 200
    diagnostics = chat.json()["data"]["diagnostics"]
    assert diagnostics["tenant_id"] == "tenant-a"
    assert diagnostics["policy_id"] == "low-cost"
    assert diagnostics["policy_version"] == 1
    assert diagnostics["governance_policy_id"] == "default-governance"
    assert diagnostics["policy_candidate_ids"] == ["provider_a", "provider_b"]
    references = audit.json()["data"]["references"]
    assert len(references) == 1
    assert references[0]["policy_id"] == "low-cost"
    assert references[0]["candidate_ids"] == ["provider_a", "provider_b"]


def test_v2_governance_filters_router_candidates_and_rejects_empty_sets() -> None:
    primary = QueueRouter([decision("provider_a"), decision("provider_a")])
    fallback = QueueRouter([decision("provider_b")])
    engine, _, _ = make_engine(primary, fallback)
    FakeProvider.responses = {"provider_b": "governed answer"}
    client = TestClient(create_app(engine=engine))
    headers = {"X-Tenant-ID": "tenant-a", "X-Developer-Mode": "true"}
    policy = client.put(
        "/api/v2/governance/policy",
        headers=headers,
        json={"allowed_provider_ids": ["provider_b"]},
    )
    response = client.post(
        "/api/v2/chat", headers=headers, json={"conversation_id": "one", "message": "hello"}
    )

    assert policy.status_code == 200
    assert response.status_code == 200
    assert [candidate.id for candidate in primary.calls[0].available_providers] == ["provider_b"]
    assert response.json()["data"]["diagnostics"]["selected_provider"] == "provider_b"

    denied = client.put(
        "/api/v2/governance/policy", headers=headers, json={"denied_provider_ids": ["provider_a", "provider_b"]}
    )
    empty = client.post(
        "/api/v2/chat", headers=headers, json={"conversation_id": "two", "message": "hello"}
    )

    assert denied.status_code == 200
    assert empty.status_code == 403
    assert empty.json()["error"]["code"] == "GOVERNANCE_POLICY_DENIED"
    assert empty.json()["diagnostics"]["failure_stage"] == "governance"
    assert len(primary.calls) == 1  # No Router call occurs when governance leaves no candidate.


def test_v2_tenant_conversations_are_isolated() -> None:
    engine, _, _ = make_engine(QueueRouter([decision("provider_a"), decision("provider_a")]), QueueRouter([]))
    FakeProvider.responses = {"provider_a": "answer"}
    client = TestClient(create_app(engine=engine))

    first = client.post("/api/v2/chat", headers={"X-Tenant-ID": "tenant-a"}, json={"conversation_id": "shared", "message": "a"})
    second = client.post("/api/v2/chat", headers={"X-Tenant-ID": "tenant-b"}, json={"conversation_id": "shared", "message": "b"})

    assert first.status_code == 200 and second.status_code == 200
    assert engine.conversation_state("shared", "tenant-a").original_query == "a"
    assert engine.conversation_state("shared", "tenant-b").original_query == "b"

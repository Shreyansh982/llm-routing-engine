"""Minimal V2.5 policy administration UI backed exclusively by additive V2 APIs."""

from __future__ import annotations

import streamlit as st

from dashboard.utils.api_client import RoutingEngineApiClient


POLICY_KINDS = [
    "legacy-balanced",
    "balanced",
    "cheapest",
    "lowest_latency",
    "highest_quality",
    "compliance",
]


def render(client: RoutingEngineApiClient) -> None:
    st.title("Policy Studio")
    st.caption("Manage deterministic, tenant-scoped candidate-ordering policies. Policies never select providers.")
    tenant_id = st.text_input("Tenant ID", value=st.session_state.tenant_id, key="policy-studio-tenant")
    st.session_state.tenant_id = tenant_id.strip() or "default"

    policies_result = client.policies(st.session_state.tenant_id)
    if not policies_result.succeeded:
        st.error(policies_result.error or "Unable to load policies.")
        return
    policies = policies_result.payload.get("data", {}).get("policies", [])
    if not isinstance(policies, list):
        policies = []
    st.subheader("Active policies")
    st.dataframe(policies, use_container_width=True, hide_index=True)

    with st.expander("Create built-in policy"):
        with st.form("create-policy"):
            policy_id = st.text_input("Policy ID", placeholder="production-low-latency")
            kind = st.selectbox("Built-in policy", POLICY_KINDS)
            description = st.text_input("Description")
            activate = st.checkbox("Activate for this tenant")
            submitted = st.form_submit_button("Create policy")
        if submitted:
            result = client.create_policy(
                st.session_state.tenant_id,
                {"policy_id": policy_id, "kind": kind, "description": description, "activate": activate},
            )
            if result.succeeded:
                st.success("Policy version created.")
                st.rerun()
            else:
                st.error(result.error or "Unable to create policy.")

    if policies:
        policy_ids = [str(policy.get("policy_id")) for policy in policies if isinstance(policy, dict)]
        selected = st.selectbox("Policy to activate or evaluate", policy_ids)
        actions = st.columns(2)
        if actions[0].button("Activate selected policy", use_container_width=True):
            result = client.activate_policy(st.session_state.tenant_id, selected)
            if result.succeeded:
                st.success("Policy activated.")
                st.rerun()
            else:
                st.error(result.error or "Unable to activate policy.")
        if actions[1].button("Evaluate candidate ordering", use_container_width=True):
            result = client.evaluate_policy(
                st.session_state.tenant_id, {"routing_context": {"policy_id": selected}}
            )
            if result.succeeded:
                data = result.payload.get("data", {})
                st.session_state.policy_evaluation = data if isinstance(data, dict) else {}
            else:
                st.error(result.error or "Unable to evaluate policy.")

    evaluation = st.session_state.get("policy_evaluation")
    if isinstance(evaluation, dict):
        st.subheader("Candidate evaluation")
        st.caption("This is a deterministic metadata evaluation. No Router or provider is invoked.")
        st.dataframe(evaluation.get("rankings", []), use_container_width=True, hide_index=True)
        exclusions = evaluation.get("exclusions", [])
        if exclusions:
            st.warning("Governance exclusions")
            st.dataframe(exclusions, use_container_width=True, hide_index=True)

"""Read-only settings page limited to values published by the API or dashboard itself."""

from __future__ import annotations

import streamlit as st

from dashboard.components.common import developer_table
from dashboard.utils.api_client import RoutingEngineApiClient
from dashboard.utils.records import TECHNICAL_UNAVAILABLE


def render(client: RoutingEngineApiClient) -> None:
    st.title("Settings")
    st.caption("Read-only. API keys and private backend configuration are never read by the dashboard.")
    st.write(f"Routing Engine API: `{st.session_state.api_base_url}`")
    developer_mode = st.session_state.developer_mode
    providers = client.providers(developer_mode=developer_mode)
    router_health = client.router_health(developer_mode=developer_mode)
    rows: list[dict[str, object]] = []
    data = providers.payload.get("data", {}) if providers.succeeded else {}
    if isinstance(data, dict) and isinstance(data.get("providers"), list):
        diagnostics = data.get("diagnostics", {})
        configured_by_id = {
            item["id"]: item
            for item in diagnostics.get("configured_providers", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        } if isinstance(diagnostics, dict) else {}
        retry_limit = diagnostics.get("retry_limit", TECHNICAL_UNAVAILABLE) if isinstance(diagnostics, dict) else TECHNICAL_UNAVAILABLE
        rows = [
            {
                "provider_id": item.get("id"),
                "enabled": item.get("enabled"),
                "backend": configured_by_id.get(item.get("id"), {}).get("backend", TECHNICAL_UNAVAILABLE),
                "model": configured_by_id.get(item.get("id"), {}).get("model", TECHNICAL_UNAVAILABLE),
                "backend_url": configured_by_id.get(item.get("id"), {}).get("endpoint", TECHNICAL_UNAVAILABLE),
                "timeout": configured_by_id.get(item.get("id"), {}).get("timeout", TECHNICAL_UNAVAILABLE),
                "retry_limit": retry_limit,
            }
            for item in data["providers"]
            if isinstance(item, dict)
        ]
    if providers.error:
        st.warning(providers.error)
    developer_table(rows, st.session_state.developer_mode, ["provider_id", "enabled"])
    router_data = router_health.payload.get("data", {}) if router_health.succeeded else {}
    router_diagnostics = router_data.get("diagnostics", {}) if isinstance(router_data, dict) else {}
    router_rows = router_diagnostics.get("configured_routers", []) if isinstance(router_diagnostics, dict) else []
    if st.session_state.developer_mode:
        st.subheader("Configured Routers")
        st.dataframe(router_rows, use_container_width=True, hide_index=True)
    else:
        st.caption("Enable Developer Mode to view backend, model, endpoint, timeout, and retry configuration.")

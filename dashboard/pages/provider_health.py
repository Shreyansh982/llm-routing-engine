"""Public API health checks for configured router and provider IDs."""

from __future__ import annotations

from time import perf_counter

import streamlit as st

from dashboard.components.common import developer_table, status_badge
from dashboard.utils.api_client import RoutingEngineApiClient
from dashboard.utils.records import TECHNICAL_UNAVAILABLE
from dashboard.utils.state import timestamp


def render(client: RoutingEngineApiClient) -> None:
    st.title("Provider Health")
    if st.button("Refresh health", type="primary") or "health_snapshot" not in st.session_state:
        st.session_state.health_snapshot = _refresh(client)
    snapshot = st.session_state.health_snapshot
    st.caption(f"Last health check: {snapshot['timestamp']}")
    if snapshot["error"]:
        st.warning(snapshot["error"])

    st.subheader("Routers")
    router_rows = snapshot["routers"]
    for row in router_rows:
        status_badge(str(row["status"]))
    developer_table(router_rows, st.session_state.developer_mode, ["id", "status"])

    st.subheader("Providers")
    provider_rows = snapshot["providers"]
    developer_table(provider_rows, st.session_state.developer_mode, ["id", "enabled", "status"])
    if not st.session_state.developer_mode:
        st.caption("Enable Developer Mode to view API reachability and health-check timing.")


def _refresh(client: RoutingEngineApiClient) -> dict[str, object]:
    started = perf_counter()
    developer_mode = st.session_state.developer_mode
    router_result = client.router_health(developer_mode=developer_mode)
    provider_result = client.provider_health()
    list_result = client.providers(developer_mode=developer_mode)
    elapsed_ms = (perf_counter() - started) * 1000
    routers: list[dict[str, object]] = []
    router_data = router_result.payload.get("data", {}) if router_result.succeeded else {}
    router_config_by_id: dict[str, dict[str, object]] = {}
    if isinstance(router_data, dict):
        diagnostics = router_data.get("diagnostics", {})
        if isinstance(diagnostics, dict) and isinstance(diagnostics.get("configured_routers"), list):
            router_config_by_id = {
                item["id"]: item
                for item in diagnostics["configured_routers"]
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
        for router_id, status in router_data.items():
            if router_id == "diagnostics":
                continue
            config = router_config_by_id.get(router_id, {})
            routers.append(
                {
                    "id": router_id,
                    "backend": config.get("backend", TECHNICAL_UNAVAILABLE),
                    "configured_model": config.get("model", TECHNICAL_UNAVAILABLE),
                    "status": status,
                    "response_time_ms": round(router_result.elapsed_ms, 2),
                    "api_reachability": router_result.succeeded,
                }
            )
    statuses = provider_result.payload.get("data", {}) if provider_result.succeeded else {}
    enabled_by_id: dict[str, bool] = {}
    provider_data = list_result.payload.get("data", {}) if list_result.succeeded else {}
    provider_config_by_id: dict[str, dict[str, object]] = {}
    if isinstance(provider_data, dict) and isinstance(provider_data.get("providers"), list):
        enabled_by_id = {
            item["id"]: item["enabled"]
            for item in provider_data["providers"]
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        diagnostics = provider_data.get("diagnostics", {})
        if isinstance(diagnostics, dict) and isinstance(diagnostics.get("configured_providers"), list):
            provider_config_by_id = {
                item["id"]: item
                for item in diagnostics["configured_providers"]
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
    providers = [
        {
            "id": provider_id,
            "enabled": enabled_by_id.get(provider_id, TECHNICAL_UNAVAILABLE),
            "backend": provider_config_by_id.get(provider_id, {}).get("backend", TECHNICAL_UNAVAILABLE),
            "configured_model": provider_config_by_id.get(provider_id, {}).get("model", TECHNICAL_UNAVAILABLE),
            "status": status,
            "response_time_ms": round(provider_result.elapsed_ms, 2),
            "api_reachability": provider_result.succeeded,
        }
        for provider_id, status in statuses.items()
    ] if isinstance(statuses, dict) else []
    error = router_result.error or provider_result.error or list_result.error
    return {"timestamp": timestamp(), "routers": routers, "providers": providers, "error": error, "elapsed_ms": elapsed_ms}

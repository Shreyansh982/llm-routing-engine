"""Developer-facing, API-observable routing request details."""

from __future__ import annotations

import streamlit as st

from dashboard.components.common import status_badge


def render() -> None:
    st.title("Routing Details")
    record = st.session_state.last_request
    if not record:
        st.info("Send a chat request to inspect its observable routing details.")
        return

    if not st.session_state.developer_mode:
        st.info("Enable Developer Mode in the sidebar to view engineering diagnostics.")
        st.metric("Request latency", f"{record['latency_ms']:.0f} ms")
        status_badge(record["status"])
        return

    diagnostics = record.get("diagnostics", {})
    if not isinstance(diagnostics, dict) or not diagnostics:
        if not record.get("developer_mode", False):
            st.info("Developer Mode was disabled for this request, so the backend intentionally omitted diagnostics.")
            return
        st.error("Developer Mode was enabled, but the backend returned no diagnostics.")
        return
    first, second, third = st.columns(3)
    first.metric("Request ID", str(diagnostics.get("request_id", record["request_id"]))[:8])
    second.metric("Router action", record["action"] or "Unavailable")
    third.metric("Latency", f"{record['latency_ms']:.0f} ms")
    st.write(f"Conversation ID: `{record['conversation_id']}`")
    status_badge(record["status"])
    st.success("Request succeeded with diagnostics.") if record["status"] == "success" else st.error(
        "Request failed with diagnostics."
    )

    with st.expander("Router and provider diagnostics", expanded=True):
        st.write(f"Router used: `{diagnostics.get('router_used')}`")
        st.write(f"Selected provider: `{diagnostics.get('provider_id', diagnostics.get('selected_provider'))}`")
        st.write(f"Provider backend: `{diagnostics.get('provider_backend', diagnostics.get('backend'))}`")
        st.write(f"Configured model: `{diagnostics.get('configured_model', diagnostics.get('model'))}`")
        st.write(f"Routing reason: {diagnostics.get('routing_reason')}")
        st.write(f"Fallback used: {diagnostics.get('fallback_used')}")
        st.write(f"Retry count: {diagnostics.get('retry_count')}")
        st.json({"capabilities_considered": diagnostics.get("capabilities_considered", [])})
        st.json({"latency_breakdown_ms": diagnostics.get("latency_breakdown", {})})
        st.write(f"Failure stage: `{diagnostics.get('failure_stage', 'none')}`")
        st.write(f"Failure reason: `{diagnostics.get('failure_reason', 'NONE')}`")
        st.write(f"HTTP status: {diagnostics.get('http_status', record['http_status']) or 'No response'}")
        if diagnostics.get("upstream_http_status") is not None:
            st.write(f"Upstream HTTP status: {diagnostics['upstream_http_status']}")
        if diagnostics.get("failure_level"):
            st.error(f"Failure level: {diagnostics['failure_level']}")
        if diagnostics.get("provider_error"):
            st.error(f"Provider error: {diagnostics['provider_error']}")
        if record["error"]:
            st.error(record["error"])

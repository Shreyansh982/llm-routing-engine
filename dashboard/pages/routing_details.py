"""Developer-facing, API-observable routing request details."""

from __future__ import annotations

from typing import Any

import plotly.graph_objects as go
import streamlit as st

from dashboard.components.common import PLOTLY_CONFIG, request_status_card, status_badge


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
    first, second = st.columns([1, 2])
    first.metric("Request ID", str(diagnostics.get("request_id", record["request_id"]))[:8])
    second.caption(f"Conversation ID: `{record['conversation_id']}`")
    request_status_card(record["status"], record["action"], float(record["latency_ms"]))

    _render_routing_explanation(record, diagnostics)
    _render_routing_timeline(record, diagnostics)

    with st.expander("Router and provider diagnostics", expanded=True):
        st.write(f"Router used: `{diagnostics.get('router_used')}`")
        st.write(f"Selected provider: `{diagnostics.get('provider_id', diagnostics.get('selected_provider'))}`")
        st.write(f"Provider backend: `{diagnostics.get('provider_backend', diagnostics.get('backend'))}`")
        st.write(f"Configured model: `{diagnostics.get('configured_model', diagnostics.get('model'))}`")
        st.write(f"Fallback used: {diagnostics.get('fallback_used')}")
        st.write(f"Retry count: {diagnostics.get('retry_count')}")
        _render_latency_table(diagnostics.get("latency_breakdown"))
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


def _render_routing_explanation(record: dict[str, Any], diagnostics: dict[str, Any]) -> None:
    """Present backend-provided routing facts; never reproduce routing logic in the UI."""
    st.subheader("Routing Explanation")
    intent, selected, reason = st.columns(3)
    intent.markdown("**Intent**")
    intent.write(record.get("prompt", "Not available"))
    selected.markdown("**Selected Provider**")
    provider_id = diagnostics.get("provider_id", diagnostics.get("selected_provider", "No provider selected"))
    selected.write(f"`{provider_id}`")
    selected.caption(f"via {diagnostics.get('router_used', 'No Router recorded')}")
    reason.markdown("**Reason**")
    reason.write(diagnostics.get("routing_reason", "No routing reason was returned."))
    if diagnostics.get("fallback_used"):
        reason.caption("Fallback ladder used")

    st.markdown("**Required Capabilities**")
    st.caption("The API exposes the capability descriptors considered by the Router.")
    capabilities = diagnostics.get("capabilities_considered", [])
    if capabilities:
        st.dataframe(_capability_rows(capabilities), use_container_width=True, hide_index=True)
    else:
        st.caption("No capability descriptors were returned for this request.")


def _capability_rows(capabilities: Any) -> list[dict[str, str]]:
    if not isinstance(capabilities, list):
        return []
    rows: list[dict[str, str]] = []
    for item in capabilities:
        if not isinstance(item, dict):
            continue
        descriptor = item.get("capabilities", {})
        descriptor = descriptor if isinstance(descriptor, dict) else {}
        strengths = descriptor.get("strengths", [])
        rows.append(
            {
                "Provider ID": str(item.get("id", "Unknown")),
                "Strengths": ", ".join(str(value) for value in strengths) if isinstance(strengths, list) else "",
                "Speed tier": str(descriptor.get("speed_tier", "")),
                "Context size": str(descriptor.get("context_size", "")),
            }
        )
    return rows


def _render_latency_table(latency: Any) -> None:
    if not isinstance(latency, dict):
        return
    st.markdown("**Engine latency**")
    st.dataframe(
        [
            {"Stage": "Router", "Latency (ms)": f"{float(latency.get('router_ms', 0) or 0):.2f}"},
            {"Stage": "Provider adapter", "Latency (ms)": f"{float(latency.get('provider_ms', 0) or 0):.2f}"},
            {"Stage": "Response gateway", "Latency (ms)": f"{float(latency.get('gateway_ms', 0) or 0):.2f}"},
            {"Stage": "Total", "Latency (ms)": f"{float(latency.get('total_ms', 0) or 0):.2f}"},
        ],
        use_container_width=True,
        hide_index=True,
    )


def _render_routing_timeline(record: dict[str, Any], diagnostics: dict[str, Any]) -> None:
    latency = diagnostics.get("latency_breakdown")
    if not isinstance(latency, dict):
        return
    stages = [
        ("Router", latency.get("router_ms", 0), "#4C78A8"),
        ("Provider adapter", latency.get("provider_ms", 0), "#F58518"),
        ("Response gateway", latency.get("gateway_ms", 0), "#54A24B"),
    ]
    total_engine_ms = float(latency.get("total_ms", 0) or 0)
    client_overhead_ms = max(float(record.get("latency_ms", 0) or 0) - total_engine_ms, 0)
    if client_overhead_ms:
        stages.append(("API/client overhead", client_overhead_ms, "#B8B8B8"))

    st.subheader("Routing Timeline")
    timeline = go.Figure()
    cursor = 0.0
    for name, duration, color in stages:
        duration_ms = max(float(duration or 0), 0)
        timeline.add_trace(
            go.Bar(
                x=[duration_ms],
                y=["Request lifecycle"],
                base=[cursor],
                orientation="h",
                marker_color=color,
                name=name,
                text=f"{duration_ms:.0f} ms",
                textposition="inside",
                hovertemplate=f"{name}: %{{x:.2f}} ms<extra></extra>",
            )
        )
        cursor += duration_ms
    timeline.update_layout(
        barmode="overlay",
        height=210,
        xaxis_title="Elapsed time (ms)",
        legend_title="Stage",
        margin=dict(l=20, r=20, t=30, b=40),
    )
    st.plotly_chart(timeline, use_container_width=True, config=PLOTLY_CONFIG)

    latency_chart = go.Figure(
        go.Bar(
            x=[name for name, _, _ in stages],
            y=[float(duration or 0) for _, duration, _ in stages],
            marker_color=[color for _, _, color in stages],
            text=[f"{float(duration or 0):.0f} ms" for _, duration, _ in stages],
            textposition="auto",
        )
    )
    latency_chart.update_layout(
        title="Latency Breakdown",
        yaxis_title="Milliseconds",
        height=310,
        margin=dict(l=20, r=20, t=50, b=40),
    )
    st.plotly_chart(latency_chart, use_container_width=True, config=PLOTLY_CONFIG)

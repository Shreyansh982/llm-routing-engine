"""Shared Streamlit display primitives."""

from __future__ import annotations

from typing import Any

import streamlit as st

from dashboard.utils.records import TECHNICAL_UNAVAILABLE


PLOTLY_CONFIG = {"displayModeBar": False, "responsive": True}


def api_unavailable(label: str) -> None:
    st.caption(f"{label}: {TECHNICAL_UNAVAILABLE}")


def status_badge(status: str) -> None:
    if status == "healthy" or status == "success":
        st.success(status.capitalize())
    elif status in {"degraded", "offline"}:
        st.warning(status.capitalize())
    else:
        st.error(status.capitalize())


def request_status_card(status: str, action: str, latency_ms: float) -> None:
    """A single compact status treatment for routing-detail requests."""
    with st.container(border=True):
        state = "Request succeeded" if status == "success" else "Request failed"
        if status == "success":
            st.success(state)
        else:
            st.error(state)
        status_column, action_column, latency_column = st.columns(3)
        status_column.metric("Status", status.capitalize())
        action_column.metric("Router action", action or "Unavailable")
        latency_column.metric("End-to-end latency", f"{latency_ms:.0f} ms")


def developer_table(rows: list[dict[str, Any]], developer_mode: bool, public_columns: list[str]) -> None:
    if developer_mode:
        st.dataframe(rows, use_container_width=True, hide_index=True)
        return
    st.dataframe(
        [{column: row.get(column) for column in public_columns} for row in rows],
        use_container_width=True,
        hide_index=True,
    )

"""Shared Streamlit display primitives."""

from __future__ import annotations

from typing import Any

import streamlit as st

from dashboard.utils.records import TECHNICAL_UNAVAILABLE


def api_unavailable(label: str) -> None:
    st.caption(f"{label}: {TECHNICAL_UNAVAILABLE}")


def status_badge(status: str) -> None:
    if status == "healthy" or status == "success":
        st.success(status.capitalize())
    elif status in {"degraded", "offline"}:
        st.warning(status.capitalize())
    else:
        st.error(status.capitalize())


def developer_table(rows: list[dict[str, Any]], developer_mode: bool, public_columns: list[str]) -> None:
    if developer_mode:
        st.dataframe(rows, use_container_width=True, hide_index=True)
        return
    st.dataframe(
        [{column: row.get(column) for column in public_columns} for row in rows],
        use_container_width=True,
        hide_index=True,
    )

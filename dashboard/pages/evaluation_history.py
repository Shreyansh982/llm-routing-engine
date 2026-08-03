"""Searchable, exportable in-session evaluation history."""

from __future__ import annotations

import streamlit as st

from dashboard.components.common import developer_table
from dashboard.utils.records import to_csv, to_json


def render() -> None:
    st.title("Evaluation History")
    records = list(st.session_state.evaluation_history)
    if not records:
        st.info("Evaluation records appear after Chat requests.")
        return

    filters = st.columns(3)
    search = filters[0].text_input("Search prompt or response")
    status = filters[1].selectbox("Status", ["All", "success", "failure"])
    order = filters[2].selectbox("Sort latency", ["Newest first", "Oldest first", "Slowest first", "Fastest first"])
    filtered = [
        record
        for record in records
        if (not search or search.lower() in f"{record['prompt']} {record['response']}".lower())
        and (status == "All" or record["status"] == status)
    ]
    if order == "Newest first":
        filtered.reverse()
    elif order == "Slowest first":
        filtered.sort(key=lambda record: record["latency_ms"], reverse=True)
    elif order == "Fastest first":
        filtered.sort(key=lambda record: record["latency_ms"])

    public_columns = ["timestamp", "conversation_id", "prompt", "response", "latency_ms", "status", "action"]
    developer_table(filtered, st.session_state.developer_mode, public_columns)
    dataset = st.session_state.evaluation_dataset
    st.caption(f"Evaluation dataset: {len(dataset)} interaction(s) captured while Evaluation Mode was enabled.")
    exports = st.columns(2)
    exports[0].download_button("Export dataset CSV", to_csv(dataset), "evaluation_dataset.csv", "text/csv")
    exports[1].download_button("Export dataset JSON", to_json(dataset), "evaluation_dataset.json", "application/json")

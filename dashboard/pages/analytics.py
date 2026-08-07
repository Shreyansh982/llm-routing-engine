"""Live client-side analytics calculated from the evaluation history."""

from __future__ import annotations

from statistics import median
from collections import Counter

import plotly.graph_objects as go
import streamlit as st

from dashboard.components.common import PLOTLY_CONFIG
from dashboard.utils.records import TECHNICAL_UNAVAILABLE


def render() -> None:
    st.title("Analytics")
    records = st.session_state.evaluation_history
    if not records:
        st.info("Send requests from Chat to populate live evaluation metrics.")
        return

    successful = [record for record in records if record["status"] == "success"]
    failed = len(records) - len(successful)
    latencies = [record["latency_ms"] for record in records]
    retries = [record for record in records if (record["retry_count"] or 0) > 0]
    primary_metrics = st.columns(4)
    primary_metrics[0].metric("Total requests", len(records))
    primary_metrics[1].metric("Successful", len(successful))
    primary_metrics[2].metric("Failed", failed)
    primary_metrics[3].metric("Success rate", f"{len(successful) / len(records):.0%}")
    secondary_metrics = st.columns(3)
    secondary_metrics[0].metric("Average latency", f"{sum(latencies) / len(latencies):.0f} ms")
    secondary_metrics[1].metric("Median latency", f"{median(latencies):.0f} ms")
    secondary_metrics[2].metric("Retry rate", f"{len(retries) / len(records):.0%}")
    fallback_values = [record["fallback_used"] for record in records if isinstance(record["fallback_used"], bool)]
    fallback_caption = (
        f"Fallback rate: {sum(fallback_values) / len(fallback_values):.0%}"
        if fallback_values
        else f"Fallback rate: {TECHNICAL_UNAVAILABLE}"
    )
    st.caption(fallback_caption)

    left, right = st.columns(2)
    with left:
        latency_chart = go.Figure(
            go.Scatter(
                x=[record["timestamp"] for record in records],
                y=latencies,
                mode="lines+markers",
                name="Latency",
            )
        )
        latency_chart.update_layout(title="Latency over time", yaxis_title="Milliseconds", height=330)
        st.plotly_chart(latency_chart, use_container_width=True, config=PLOTLY_CONFIG)
    with right:
        outcome_chart = go.Figure(
            go.Bar(x=["Success", "Failure"], y=[len(successful), failed], marker_color=["#2ca02c", "#d62728"])
        )
        outcome_chart.update_layout(title="Success vs Failure", height=330)
        st.plotly_chart(outcome_chart, use_container_width=True, config=PLOTLY_CONFIG)

    _success_rate_trend(records)

    if st.session_state.developer_mode:
        one, two = st.columns(2)
        _distribution_chart(one, "Provider usage", records, "selected_provider")
        _distribution_chart(two, "Router usage", records, "router_used")
    else:
        st.info("Enable Developer Mode to view provider and router distributions.")


def _distribution_chart(container: object, title: str, records: list[dict[str, object]], field: str) -> None:
    values = [str(record[field]) for record in records if record.get(field) != TECHNICAL_UNAVAILABLE]
    if not values:
        container.info(f"{title} is available after Developer Mode requests are recorded.")
        return
    counts = Counter(values)
    chart = go.Figure(go.Bar(x=list(counts), y=list(counts.values())))
    chart.update_layout(title=title, height=300)
    container.plotly_chart(chart, use_container_width=True, config=PLOTLY_CONFIG)


def _success_rate_trend(records: list[dict[str, object]]) -> None:
    successes = 0
    rates: list[float] = []
    for index, record in enumerate(records, start=1):
        successes += record["status"] == "success"
        rates.append(successes / index * 100)
    chart = go.Figure(
        go.Scatter(
            x=[record["timestamp"] for record in records],
            y=rates,
            mode="lines+markers",
            name="Cumulative success rate",
        )
    )
    chart.update_layout(
        title="Success-rate trend",
        yaxis=dict(title="Success rate (%)", range=[0, 100]),
        height=300,
    )
    st.plotly_chart(chart, use_container_width=True, config=PLOTLY_CONFIG)

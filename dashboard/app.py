"""Streamlit entry point for evaluating the Routing Engine through FastAPI only."""

from __future__ import annotations

import streamlit as st

from dashboard.pages import analytics, chat, evaluation_history, provider_health, routing_details, settings
from dashboard.utils.api_client import RoutingEngineApiClient
from dashboard.utils.state import initialize


PAGES = {
    "Chat": chat.render,
    "Routing Details": routing_details.render,
    "Analytics": analytics.render,
    "Provider Health": provider_health.render,
    "Evaluation History": evaluation_history.render,
    "Settings": settings.render,
}


def main() -> None:
    st.set_page_config(page_title="Routing Engine Evaluation", page_icon="🧭", layout="wide")
    initialize()
    with st.sidebar:
        st.title("Evaluation Dashboard")
        page = st.radio("Navigation", list(PAGES), label_visibility="collapsed")
        st.divider()
        st.session_state.developer_mode = st.toggle("Developer Mode", value=st.session_state.developer_mode)
        st.session_state.evaluation_mode = st.toggle("Evaluation Mode", value=st.session_state.evaluation_mode)
        st.caption("Evaluation Mode records each API interaction in this session for export.")
        conversation_ids = list(st.session_state.conversations)
        active_index = conversation_ids.index(st.session_state.active_conversation_id)
        st.session_state.active_conversation_id = st.selectbox(
            "Active conversation",
            conversation_ids,
            index=active_index,
            format_func=lambda conversation_id: f"Conversation {conversation_id[:8]}",
        )
        with st.expander("API connection"):
            st.session_state.api_base_url = st.text_input(
                "FastAPI base URL", value=st.session_state.api_base_url
            ).rstrip("/")
            st.caption("The dashboard calls this API only; it never calls cloud providers directly.")

    client = RoutingEngineApiClient(st.session_state.api_base_url)
    render = PAGES[page]
    if page in {"Chat", "Provider Health", "Settings"}:
        render(client)
    else:
        render()


if __name__ == "__main__":
    main()

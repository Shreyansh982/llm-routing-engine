"""ChatGPT-style evaluation chat page."""

from __future__ import annotations

import streamlit as st

from dashboard.components.common import status_badge
from dashboard.utils.api_client import RoutingEngineApiClient
from dashboard.utils.records import new_record
from dashboard.utils.state import append_message, clear_active_conversation, start_new_conversation, timestamp


def render(client: RoutingEngineApiClient) -> None:
    st.title("Chat")
    st.caption("All messages are sent to the Routing Engine through its public FastAPI endpoint.")
    conversation_id = st.session_state.active_conversation_id

    actions = st.columns(3)
    if actions[0].button("New conversation", use_container_width=True):
        start_new_conversation()
        st.rerun()
    if actions[1].button("Clear conversation", use_container_width=True):
        clear_active_conversation()
        st.rerun()
    if actions[2].button("Retry last request", use_container_width=True, disabled=not st.session_state.last_request):
        _send(client, st.session_state.last_request["prompt"], retry=True)
        st.rerun()

    for message in st.session_state.conversations.get(conversation_id, []):
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    with st.form("chat-form", clear_on_submit=True):
        prompt = st.text_area("Message", placeholder="Enter a prompt to evaluate…", height=120)
        sent = st.form_submit_button("Send", use_container_width=True)
    if sent:
        if not prompt.strip():
            st.warning("Enter a message before sending.")
        else:
            _send(client, prompt.strip(), retry=False)
            st.rerun()


def _send(client: RoutingEngineApiClient, prompt: str, retry: bool) -> None:
    conversation_id = st.session_state.active_conversation_id
    append_message(conversation_id, "user", prompt)
    with st.spinner("Routing request…"):
        result = client.chat(
            conversation_id, prompt, retry=retry, developer_mode=st.session_state.developer_mode
        )
        conversation = client.conversation(conversation_id) if result.succeeded else None

    data = result.payload.get("data", {}) if result.succeeded else {}
    response = data.get("response") if isinstance(data, dict) else None
    action = data.get("action") if isinstance(data, dict) else None
    diagnostics = data.get("diagnostics") if isinstance(data, dict) else result.payload.get("diagnostics")
    if not isinstance(response, str):
        response = _failure_message(diagnostics) if isinstance(diagnostics, dict) else (
            result.error or "The Routing Engine could not process this request."
        )
    append_message(conversation_id, "assistant", response)

    retry_count = None
    if conversation and conversation.succeeded:
        state = conversation.payload.get("data", {})
        if isinstance(state, dict) and isinstance(state.get("attempt_count"), int):
            retry_count = state["attempt_count"]
    status = "success" if result.succeeded else "failure"
    record = new_record(
        timestamp=timestamp(),
        conversation_id=conversation_id,
        prompt=prompt,
        response=response,
        status=status,
        action=action if isinstance(action, str) else None,
        latency_ms=result.elapsed_ms,
        retry_count=retry_count,
        http_status=result.status_code,
        error=result.error,
        diagnostics=diagnostics if isinstance(diagnostics, dict) else None,
        developer_mode=st.session_state.developer_mode or isinstance(diagnostics, dict),
    )
    st.session_state.evaluation_history.append(record)
    if st.session_state.evaluation_mode:
        st.session_state.evaluation_dataset.append(record)
    st.session_state.last_request = record
    if result.succeeded:
        status_badge("success")
    else:
        st.error(response)


def _failure_message(diagnostics: dict[str, object]) -> str:
    """Prefer the diagnosed upstream classification over a generic API error message."""
    stage = diagnostics.get("failure_stage", "unknown")
    reason = diagnostics.get("failure_reason", "UNKNOWN")
    upstream_status = diagnostics.get("upstream_http_status", diagnostics.get("http_status"))
    suffix = f" (upstream HTTP {upstream_status})" if upstream_status is not None else ""
    return f"{stage} failure: {reason}{suffix}"

"""Session state management for the evaluation-only dashboard."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import streamlit as st


def new_conversation_id() -> str:
    return str(uuid4())


def initialize() -> None:
    defaults = {
        "api_base_url": "http://127.0.0.1:8000/api/v1",
        "developer_mode": False,
        "evaluation_mode": True,
        "active_conversation_id": new_conversation_id(),
        "conversations": {},
        "evaluation_history": [],
        "evaluation_dataset": [],
        "last_request": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)
    st.session_state.conversations.setdefault(st.session_state.active_conversation_id, [])


def start_new_conversation() -> str:
    conversation_id = new_conversation_id()
    st.session_state.active_conversation_id = conversation_id
    st.session_state.conversations[conversation_id] = []
    st.session_state.last_request = None
    return conversation_id


def clear_active_conversation() -> None:
    st.session_state.conversations[st.session_state.active_conversation_id] = []
    st.session_state.last_request = None


def append_message(conversation_id: str, role: str, content: str) -> None:
    st.session_state.conversations.setdefault(conversation_id, []).append({"role": role, "content": content})


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()

from __future__ import annotations

import json
from csv import DictReader
from io import StringIO

from dashboard.pages.chat import _failure_message
from dashboard.pages.routing_details import _capability_rows
from dashboard.utils.records import TECHNICAL_UNAVAILABLE, new_record, to_csv, to_json


def test_evaluation_record_never_invents_or_exposes_private_routing_metadata() -> None:
    record = new_record(
        timestamp="2026-01-01T00:00:00+00:00",
        conversation_id="conversation",
        prompt="hello",
        response="world",
        status="success",
        action="ANSWER",
        latency_ms=12.345,
        retry_count=0,
        http_status=200,
        error=None,
    )
    assert record["backend"] == TECHNICAL_UNAVAILABLE
    assert record["model"] == TECHNICAL_UNAVAILABLE
    assert record["latency_ms"] == 12.35
    assert "api_key" not in record


def test_evaluation_record_uses_opt_in_backend_diagnostics() -> None:
    record = new_record(
        timestamp="2026-01-01T00:00:00+00:00",
        conversation_id="conversation",
        prompt="hello",
        response="world",
        status="success",
        action="ANSWER",
        latency_ms=1,
        retry_count=0,
        http_status=200,
        error=None,
        diagnostics={"router_used": "primary_router", "backend": "openrouter", "model": "configured"},
    )
    assert record["router_used"] == "primary_router"
    assert record["backend"] == "openrouter"
    assert record["developer_mode"] is False
    assert record["failure_stage"] == "Not exposed by current API"
    assert record["configured_model"] == "configured"


def test_history_exports_are_valid_csv_and_json() -> None:
    record = new_record(
        timestamp="2026-01-01T00:00:00+00:00",
        conversation_id="conversation",
        prompt="hello",
        response="world",
        status="success",
        action="ANSWER",
        latency_ms=1,
        retry_count=0,
        http_status=200,
        error=None,
    )
    assert "conversation_id" in to_csv([record])
    assert json.loads(to_json([record]))[0]["conversation_id"] == "conversation"


def test_csv_export_includes_provider_fields_for_success_and_failure_records() -> None:
    success = new_record(
        timestamp="2026-01-01T00:00:00+00:00",
        conversation_id="success",
        prompt="hello",
        response="world",
        status="success",
        action="ANSWER",
        latency_ms=1,
        retry_count=0,
        http_status=200,
        error=None,
        diagnostics={"provider_id": "provider_a", "upstream_http_status": None},
    )
    failure = new_record(
        timestamp="2026-01-01T00:01:00+00:00",
        conversation_id="failure",
        prompt="hello",
        response="provider failure",
        status="failure",
        action=None,
        latency_ms=2,
        retry_count=0,
        http_status=503,
        error="Selected provider is unavailable.",
        diagnostics={
            "provider_id": "provider_a",
            "upstream_http_status": 429,
            "failure_stage": "provider",
            "failure_reason": "HTTP_429_RATE_LIMIT",
        },
    )

    rows = list(DictReader(StringIO(to_csv([success, failure]))))

    assert rows[0]["provider_id"] == "provider_a"
    assert rows[1]["upstream_http_status"] == "429"
    assert rows[1]["failure_reason"] == "HTTP_429_RATE_LIMIT"


def test_dashboard_failure_summary_prefers_provider_http_diagnostics() -> None:
    summary = _failure_message(
        {
            "failure_stage": "provider",
            "failure_reason": "HTTP_429_RATE_LIMIT",
            "upstream_http_status": 429,
        }
    )

    assert summary == "provider failure: HTTP_429_RATE_LIMIT (upstream HTTP 429)"


def test_routing_capabilities_are_shaped_for_a_readable_table() -> None:
    rows = _capability_rows(
        [
            {
                "id": "provider_a",
                "capabilities": {
                    "strengths": ["reasoning", "coding"],
                    "speed_tier": "standard",
                    "context_size": "large",
                },
            }
        ]
    )

    assert rows == [
        {
            "Provider ID": "provider_a",
            "Strengths": "reasoning, coding",
            "Speed tier": "standard",
            "Context size": "large",
        }
    ]

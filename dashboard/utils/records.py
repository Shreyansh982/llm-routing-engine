"""Evaluation-record shaping and safe export helpers."""

from __future__ import annotations

import csv
import io
import json
from typing import Any
from uuid import uuid4


TECHNICAL_UNAVAILABLE = "Not exposed by current API"

# Stable export order. Existing browser-session records may predate newly added
# diagnostics fields, so ``to_csv`` also appends any unexpected legacy fields.
CSV_FIELDNAMES = [
    "request_id",
    "timestamp",
    "conversation_id",
    "prompt",
    "response",
    "router_used",
    "selected_provider",
    "provider_id",
    "backend",
    "configured_model",
    "model",
    "latency_ms",
    "retry_count",
    "fallback_used",
    "status",
    "action",
    "http_status",
    "upstream_http_status",
    "failure_stage",
    "failure_reason",
    "error",
    "diagnostics",
    "developer_mode",
]


def new_record(
    *,
    timestamp: str,
    conversation_id: str,
    prompt: str,
    response: str,
    status: str,
    action: str | None,
    latency_ms: float,
    retry_count: int | None,
    http_status: int,
    error: str | None,
    diagnostics: dict[str, Any] | None = None,
    developer_mode: bool = False,
) -> dict[str, Any]:
    """Store API-observable facts only; never infer hidden backend metadata."""
    diagnostics = diagnostics or {}
    return {
        "request_id": diagnostics.get("request_id", str(uuid4())),
        "timestamp": timestamp,
        "conversation_id": conversation_id,
        "prompt": prompt,
        "response": response,
        "router_used": diagnostics.get("router_used", TECHNICAL_UNAVAILABLE),
        "selected_provider": diagnostics.get("selected_provider", TECHNICAL_UNAVAILABLE),
        "provider_id": diagnostics.get("provider_id", diagnostics.get("selected_provider", TECHNICAL_UNAVAILABLE)),
        "backend": diagnostics.get("provider_backend", diagnostics.get("backend", TECHNICAL_UNAVAILABLE)),
        "configured_model": diagnostics.get("configured_model", diagnostics.get("model", TECHNICAL_UNAVAILABLE)),
        # Retained for existing dashboard integrations; configured_model is the explicit field.
        "model": diagnostics.get("configured_model", diagnostics.get("model", TECHNICAL_UNAVAILABLE)),
        "latency_ms": round(latency_ms, 2),
        "retry_count": retry_count,
        "fallback_used": diagnostics.get("fallback_used", TECHNICAL_UNAVAILABLE),
        "status": status,
        "action": action or "",
        "http_status": diagnostics.get("http_status", http_status),
        "upstream_http_status": diagnostics.get("upstream_http_status", TECHNICAL_UNAVAILABLE),
        "failure_stage": diagnostics.get("failure_stage", TECHNICAL_UNAVAILABLE),
        "failure_reason": diagnostics.get("failure_reason", TECHNICAL_UNAVAILABLE),
        "error": error or "",
        "diagnostics": diagnostics,
        "developer_mode": developer_mode,
    }


def to_csv(records: list[dict[str, Any]]) -> str:
    if not records:
        return ""
    output = io.StringIO()
    fieldnames = list(CSV_FIELDNAMES)
    for record in records:
        for fieldname in record:
            if fieldname not in fieldnames:
                fieldnames.append(fieldname)
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(records)
    return output.getvalue()


def to_json(records: list[dict[str, Any]]) -> str:
    return json.dumps(records, indent=2)

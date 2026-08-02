# API Contracts

## Provider-Agnostic LLM Routing Engine

**Document Version:** 1.0

---

# Purpose

This document defines the public API contracts for the Proof of Concept (POC).

The API Layer acts as the entry point into the Routing Engine.

The API Layer is intentionally lightweight and contains **no routing logic**.

All routing decisions are delegated to the Routing Engine.

---

# API Design Principles

- RESTful
- Stateful (conversation state is managed server-side by the Routing Engine)
- JSON request/response
- Consistent response structure
- Predictable HTTP status codes
- Provider-agnostic

---

# Base URL

```
/api/v1
```

---

# Common Response Format

Every successful request returns

```json
{
    "success": true,
    "data": {}
}
```

Every failed request returns

```json
{
    "success": false,
    "error": {
        "code": "...",
        "message": "..."
    }
}
```

---

# Endpoint Summary

| Endpoint | Method | Purpose |
|----------|--------|----------|
| /chat | POST | Process a user message |
| /conversation/{id} | GET | Retrieve conversation state |
| /providers | GET | List available providers |
| /health | GET | Service health |
| /router/health | GET | Router health |
| /providers/health | GET | Provider health |

---

# 1. POST /chat

## Purpose

Primary endpoint for interacting with the Routing Engine.

---

## Request

```json
{
    "conversation_id":"12345",
    "message":"Explain OAuth2 in simple terms.",
    "retry": false
}
```

---

## Request Fields

| Field | Type | Required | Default |
|--------|------|----------|---------|
| conversation_id | string | Yes | — |
| message | string | Yes | — |
| retry | boolean | No | false |

---

## Retry Semantics

Retry is triggered by an **explicit, deterministic client signal**, not by any inference
of "user satisfaction." There is no sentiment analysis or dissatisfaction detection in the
POC.

- When `retry` is `false` (default), the request is routed normally.
- When `retry` is `true`, the Routing Engine treats it as a deterministic instruction to
  re-route the **same** `original_query`:
  1. It adds the previously used provider to `excluded_providers`.
  2. It increments `attempt_count`.
  3. It re-invokes the Router with the reduced provider set.

Before re-invoking the Router, the engine enforces two deterministic guards that make an
infinite retry loop impossible:

- If `attempt_count >= max_attempts`, the engine returns the `STOP` action **without**
  invoking the Router.
- If every enabled provider is already in `excluded_providers` (provider pool exhausted),
  the engine returns the `STOP` action **without** invoking the Router.

---

## Success Response

```json
{
    "success": true,
    "data": {
        "conversation_id":"12345",
        "action":"ANSWER",
        "response":"OAuth2 is..."
    }
}
```

The `action` field tells the client how to interpret the response:

| action | Meaning | `response` contains |
|--------|---------|---------------------|
| ANSWER | A provider answered successfully | The assistant answer |
| CLARIFY | The Router needs more information before routing | A clarifying question for the user |
| STOP | Retry limit or provider pool exhausted; no further routing | A terminal message (e.g. "Unable to produce a better response.") |

For `CLARIFY` and `STOP`, no provider is dispatched and `attempt_count` is not incremented.

---

## Error Response

```json
{
    "success": false,
    "error": {
        "code":"ROUTER_FAILURE",
        "message":"Unable to process request."
    }
}
```

---

## Internal Workflow

```
API

↓

Routing Engine

↓

Router

↓

Provider

↓

Response Gateway

↓

Client
```

---

# 2. GET /conversation/{id}

## Purpose

Retrieve runtime conversation state.

Useful for debugging the POC.

---

## Response

```json
{
    "conversation_id":"12345",
    "attempt_count":1,
    "max_attempts":3,
    "excluded_providers":[
        "provider_a"
    ]
}
```

---

# 3. GET /providers

## Purpose

Return registered providers.

This endpoint returns **provider IDs only**.

No vendor-specific implementation details should be exposed.

---

## Response

```json
{
    "providers":[
        {
            "id":"provider_a",
            "enabled":true
        },
        {
            "id":"provider_b",
            "enabled":true
        },
        {
            "id":"provider_c",
            "enabled":false
        }
    ]
}
```

---

# 4. GET /health

## Purpose

Overall service health.

---

## Response

```json
{
    "status":"healthy"
}
```

Possible values

```
healthy

degraded

unhealthy
```

---

# 5. GET /router/health

## Purpose

Verify Router availability.

Checks

- Primary Router
- Fallback Router

---

## Response

```json
{
    "primary_router":"healthy",
    "fallback_router":"healthy"
}
```

---

# 6. GET /providers/health

## Purpose

Verify provider availability.

---

## Response

```json
{
    "provider_a":"healthy",
    "provider_b":"healthy",
    "provider_c":"offline"
}
```

---

# Internal Router Contract

This contract is **internal only**.

Clients never interact with it.

---

## Router Request

```json
{
    "original_query":"...",
    "latest_user_message":"...",
    "previous_response":"...",
    "attempt":1,
    "max_attempts":3,
    "excluded_providers":[
        "provider_a"
    ],
    "available_providers":[
        {
            "id":"provider_a",
            "capabilities":{
                "strengths":["reasoning","coding"],
                "speed_tier":"standard",
                "context_size":"large"
            }
        },
        {
            "id":"provider_b",
            "capabilities":{
                "strengths":["creative_writing","summarization"],
                "speed_tier":"fast",
                "context_size":"standard"
            }
        }
    ]
}
```

### Provider Capabilities

Each provider entry carries an **abstract capability descriptor** so the Router can make an
informed decision instead of guessing between opaque IDs. These descriptors are:

- **Vendor-neutral** — they never contain a provider brand, model name, or endpoint. The
  Router still only sees `provider_a`, `provider_b`, etc.
- **Sourced from the Model Registry** — the Routing Engine reads capabilities from the
  Registry and injects them into the Router Request. The Registry remains the single owner
  of the ID → real-provider mapping.

Descriptor fields (POC):

| Field | Type | Example values |
|-------|------|----------------|
| strengths | string[] | reasoning, coding, creative_writing, summarization |
| speed_tier | string | fast, standard |
| context_size | string | standard, large |

The vocabulary is a fixed, configuration-defined enumeration. No embeddings, classifiers,
or dynamic scoring are involved.

---

## Router Response

```json
{
    "action":"ANSWER",
    "selected_provider":"provider_b",
    "confidence":0.93,
    "reason":"..."
}
```

The Router **must** emit this object using schema-constrained / structured output (see
Component Specification — Primary Router). Free-form text that merely embeds JSON is not
acceptable.

---

# Provider Adapter Contract

Every Provider Adapter must implement the same interface.

## Request

```python
generate(
    prompt
)
```

The adapter receives **only** the prompt required to produce a response. It never receives
routing internals such as `excluded_providers` or `attempt_count`, keeping the provider
boundary free of routing concerns.

---

## Response

```json
{
    "response":"..."
}
```

---

# Response Gateway Contract

## Input

```json
{
    "response":"..."
}
```

---

## Output

```json
{
    "response":"..."
}
```

The Response Gateway applies **best-effort** masking and should strip, wherever detectable:

- Provider name
- Internal metadata
- Provider-specific branding
- Internal routing information

This is a best-effort guarantee, not an absolute one (see Security Considerations for the
two-layer defense model).

---

# HTTP Status Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 400 | Invalid Request |
| 404 | Conversation Not Found |
| 422 | Validation Failed |
| 500 | Internal Error |
| 503 | Router or Provider Unavailable |

---

# Error Codes

| Code | Description |
|------|-------------|
| INVALID_REQUEST | Request validation failed |
| INVALID_CONVERSATION | Conversation not found |
| ROUTER_TIMEOUT | Router timed out |
| ROUTER_FAILURE | Router unavailable |
| INVALID_ROUTER_RESPONSE | Invalid router JSON |
| PROVIDER_UNAVAILABLE | Selected provider unavailable |
| PROVIDER_DISABLED | Provider disabled |
| MAX_RETRIES_EXCEEDED | Retry limit reached |
| INTERNAL_ERROR | Unexpected server error |

---

# Security Considerations

Provider-identity hiding uses a **two-layer, best-effort defense**. It is not claimed to be
an absolute guarantee, because a language model can self-identify in unbounded ways.

**Layer 1 — Primary defense (prevention).** Every Provider Adapter injects a system prompt
instructing the underlying model not to reveal its identity, vendor, or model name. This
stops most leakage at the source.

**Layer 2 — Secondary defense (masking).** The Response Gateway applies deterministic
best-effort filtering of known identity strings and branding before the response leaves the
platform.

The following should not appear in the response; the two layers work together to prevent
their exposure on a best-effort basis:

- Router model name
- Provider vendor name
- Provider endpoint
- Provider API keys
- Internal routing decisions
- Retry implementation details

The client should normally receive only the assistant response.

---

# API Versioning

All endpoints are versioned.

Current version

```
/api/v1
```

Future versions

```
/api/v2

/api/v3
```

should remain backward compatible where possible.

---

# Future Endpoints (Out of Scope)

The following endpoints are intentionally excluded from the POC.

- POST /feedback
- GET /analytics
- GET /metrics
- POST /providers/register
- DELETE /providers/{id}
- POST /router/reload
- GET /costs
- GET /latency

These will be introduced in future iterations as the Routing Engine evolves beyond the Proof of Concept.

---

# Summary

The API Layer provides a clean, provider-agnostic interface into the Routing Engine.

It exposes only the functionality required by the POC while keeping all routing intelligence, provider implementations, and internal architecture hidden behind stable, versioned REST endpoints.
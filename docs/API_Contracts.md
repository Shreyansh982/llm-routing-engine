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
- Stateless (conversation state handled internally)
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
    "message":"Explain OAuth2 in simple terms."
}
```

---

## Request Fields

| Field | Type | Required |
|--------|------|----------|
| conversation_id | string | Yes |
| message | string | Yes |

---

## Success Response

```json
{
    "success": true,
    "data": {
        "conversation_id":"12345",
        "response":"OAuth2 is..."
    }
}
```

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
            "id":"provider_a"
        },
        {
            "id":"provider_b"
        },
        {
            "id":"provider_c"
        }
    ]
}
```

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

---

# Provider Adapter Contract

Every Provider Adapter must implement the same interface.

## Request

```python
generate(
    prompt,
    conversation_state
)
```

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

The output must never reveal

- Provider name
- Internal metadata
- Provider-specific branding
- Internal routing information

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

The API must never expose:

- Router model name
- Provider vendor name
- Provider endpoint
- Provider API keys
- Internal routing decisions
- Retry implementation details

The client should only receive the assistant response.

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
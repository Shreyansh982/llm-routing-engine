# API contracts

FastAPI serves V1 at `/api/v1` and V2 at `/api/v2`. Successful application responses use:

```json
{"success": true, "data": {}}
```

Controlled application failures use:

```json
{"success": false, "error": {"code": "...", "message": "..."}}
```

Set `X-Developer-Mode: true` only for trusted development/operator clients. It adds safe diagnostics to chat responses and controlled chat failures. V2 resolves `X-Tenant-ID` (default `default`) for tenant-scoped operations.

## V1 endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/chat` | Route and execute a V1 chat request. |
| GET | `/api/v1/conversation/{conversation_id}` | Read in-memory conversation retry state. |
| GET | `/api/v1/providers` | List task-provider IDs and enabled state. |
| GET | `/api/v1/health` | API liveness. |
| GET | `/api/v1/router/health` | Router availability plus backend circuit snapshots. |
| GET | `/api/v1/providers/health` | Task-provider availability plus backend circuit snapshots. |

### `POST /api/v1/chat`

Request fields:

| Field | Type | Required | Notes |
|---|---|---:|---|
| `conversation_id` | non-empty string | yes | Conversation state key. |
| `message` | non-empty string | yes | User text sent to router/provider. |
| `image` | image object | no | One validated JPEG/PNG/WebP image; see below. |
| `retry` | boolean | no | Defaults to `false`; triggers bounded conversation-level retry. |

```json
{"conversation_id":"c-1","message":"Explain this design.","retry":false}
```

Success `data` contains `conversation_id`, `action` (`ANSWER`, `RETRY`, `CLARIFY`, or `STOP`), and `response`. `ANSWER` is normally terminal from the public API; `CLARIFY`/`STOP` expose the router's safe reason text. A `retry: true` request excludes the preceding successful provider, increments state, and is bounded by `MAX_RETRIES`; it does not retry the same task model automatically.

### Image object

```json
{
  "media_type": "image/jpeg",
  "data_base64": "base64-encoded-bytes"
}
```

`media_type` is exactly `image/jpeg`, `image/png`, or `image/webp`. `data_base64` must be non-empty valid base64. The contract accepts one object, not an array. URLs, multiple images, audio, video, and image generation are unsupported. Image input forces `multimodal` classification; raw bytes are not emitted in diagnostics or retained by conversation/dashboard storage.

### V1 state and health

`GET /conversation/{conversation_id}` returns `conversation_id`, `attempt_count`, `max_attempts`, and `excluded_providers`. `GET /providers` returns `providers`, each with only `id` and `enabled`; developer mode additionally returns configured provider metadata (including backend/model/endpoint/capabilities but never keys). `GET /health` returns `{"status":"healthy"}`.

Router health returns `primary_router`, `fallback_router`, and `backends`. Provider health returns `providers` and `backends`. Each `backends` entry has a circuit `state`, `consecutive_failures`, and `half_open_inflight`. Health checks invoke configured `/models` availability checks but circuit snapshots themselves do not mutate breaker state. Developer router health includes the two configured router metadata records with API keys excluded.

## V2 endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v2/chat` | V1 chat fields plus tenant policy/governance context. |
| GET | `/api/v2/policies` | List a tenant's policies, including its active default when none exist. |
| POST | `/api/v2/policies` | Create a tenant policy; may activate it. |
| POST | `/api/v2/policies/{policy_id}/activate` | Activate an existing tenant policy. |
| POST | `/api/v2/policies/evaluate` | Return policy/governance candidate evaluation without executing chat. |
| GET | `/api/v2/governance/policy` | Get active tenant governance policy. |
| PUT | `/api/v2/governance/policy` | Create/update active tenant governance policy. |
| GET | `/api/v2/audit/policy-references` | List in-memory policy references recorded for tenant requests. |

V2 expects the optional `X-Tenant-ID` header; it defaults to `default` and accepts 1–64 alphanumeric, `_`, or `-` characters beginning with an alphanumeric character. V2 chat is the V1 request schema plus:

```json
{
  "routing_context": {
    "policy_id": "optional-policy-id",
    "data_classification": "INTERNAL",
    "region": "optional-region",
    "workload_class": "optional-workload"
  }
}
```

`routing_context` defaults to `{ "data_classification": "INTERNAL" }`. The router still does not receive concrete model/provider mappings. Governance applies allow/deny provider IDs and allowed regions; policy then ranks eligible candidates. An empty eligible set returns `403 GOVERNANCE_POLICY_DENIED`.

An invalid `X-Tenant-ID` is rejected by the tenant resolver as FastAPI's native 422 `detail` response rather than the application's `ErrorEnvelope`; this is current implementation behavior.

Create-policy request:

```json
{"policy_id":"balanced-dev","kind":"balanced","description":"optional","activate":true}
```

Supported `kind` values are `legacy-balanced`, `balanced`, `cheapest`, `lowest_latency`, `highest_quality`, and `compliance`. Policy evaluation accepts `{ "routing_context": {...}, "excluded_provider_ids": [] }` and returns tenant ID, policy, governance policy, candidate capability descriptors, rankings, and exclusions. Governance update accepts `policy_id`, `allowed_provider_ids`, `denied_provider_ids`, `allowed_regions`, and `activate`; all list values must be unique.

## Developer diagnostics

Developer chat diagnostics can include request/timestamp fields; router used and attempts; selected/initial/final provider; backend/model; routing reason and task group; fallback path/count; circuit snapshot; capability descriptors considered; retry count; router/provider/gateway/total latency; safe HTTP/failure classifications; and V2 tenant/policy/governance references. Normal chat responses omit these internals.

Diagnostics do not include API keys, upstream error bodies, raw provider responses, or raw image data. They can identify configured models/providers and, on the developer-only configuration/health endpoints described above, non-secret endpoint metadata.

## Error behavior

| Status | Code | Meaning |
|---:|---|---|
| 422 | `INVALID_REQUEST` | Chat/request validation failed. |
| 404 | `INVALID_CONVERSATION` | Requested V1 conversation does not exist. |
| 404 | `POLICY_NOT_FOUND` | V2 activation referred to an unknown policy. |
| 403 | `GOVERNANCE_POLICY_DENIED` | No candidate remains under active governance. |
| 503 | `ROUTER_FAILURE` | Both router tiers failed. |
| 503 | `PROVIDER_UNAVAILABLE` | Selected task group exhausted candidates. |
| 503 | `GATEWAY_FAILURE` | Successful provider output could not be processed. |
| 500 | `INTERNAL_ERROR` | Unexpected application exception. |

Normal failures return only the public code/message. With developer mode, failure responses attach safe diagnostics and `failure_level`; upstream HTTP status is represented as a stable classification rather than an upstream body. A provider 401/403 blocks that backend for the current request but does not trip its circuit.

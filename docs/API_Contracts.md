# API contracts

FastAPI serves V1 at `/api/v1` and V2 at `/api/v2`. Successful application responses use:

```json
{"success": true, "data": {}}
```

Controlled application failures use:

```json
{"success": false, "error": {"code": "...", "message": "..."}}
```

Developer diagnostics are disabled by default. Operators must set `DEVELOPER_MODE_ENABLED=true`; clients then request them with `X-Developer-Mode: true`. If `DEVELOPER_TOKEN` is configured, clients must also supply a matching `X-Developer-Token`. Unauthorized requests continue normally without diagnostics. V2 resolves `X-Tenant-ID` (default `default`) for tenant-scoped operations.

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

Success `data` contains `conversation_id`, `action` (`ANSWER`, `RETRY`, `CLARIFY`, or `STOP`), and `response`. `CLARIFY`/`STOP` return deterministic user-facing templates; the internal reason appears only in developer diagnostics. A `retry: true` request excludes the preceding successful provider and increments the turn's attempt count. Router `RETRY` decisions are valid only on explicit retry invocations and consume the same budget. Reaching `MAX_RETRIES` stops before another router call. A new `retry: false` message resets the count, exclusions, and last provider and replaces `original_query`, while retaining the previous answer as conversation context.

### Image object

```json
{
  "media_type": "image/jpeg",
  "data_base64": "base64-encoded-bytes"
}
```

`media_type` is exactly `image/jpeg`, `image/png`, or `image/webp`. `data_base64` must be valid base64 with a matching file signature/header. `MAX_IMAGE_BYTES` limits decoded size (default 5 MiB); encoded length is checked before decoding. This is lightweight format validation, not full image decoding. The contract accepts one object, not an array. URLs, multiple images, audio, video, and image generation are unsupported. Image input forces `multimodal` classification; raw bytes are not emitted in diagnostics or retained by conversation/dashboard storage. Retrying an image turn requires reattaching its image; omission returns 422 without consuming retry budget. Only the `has_image` marker is stored.

### V1 state and health

`GET /conversation/{conversation_id}` returns `conversation_id`, `attempt_count`, `max_attempts`, and `excluded_providers`. `GET /providers` returns `providers`, each with only `id` and `enabled`; developer mode additionally returns configured provider metadata (including backend/model/capabilities but never keys or endpoints). `GET /health` returns `{"status":"healthy"}`.

Router health returns `primary_router` and `fallback_router`. Provider health returns `providers`. Authorized developer requests additionally receive `backends`, whose entries contain `state`, `consecutive_failures`, and `half_open_inflight`. Each health operation fetches one catalog per relevant backend, using startup's provider-aware matching. Catalog availability does not guarantee inference availability. Circuit snapshots do not mutate breaker state. Developer router health includes configured router metadata with keys and endpoints excluded.

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

Invalid tenant headers and request bodies return the standard application error envelope with HTTP 422. Unknown routes and unsupported methods use the same envelope with HTTP 404/405; the `Allow` header is preserved. Developer input-error diagnostics use `failure_stage=request`.

Create-policy request:

```json
{"policy_id":"balanced-dev","kind":"balanced","description":"optional","activate":true}
```

Supported `kind` values are `legacy-balanced`, `balanced`, `cheapest`, `lowest_latency`, `highest_quality`, and `compliance`. Policy evaluation accepts `{ "routing_context": {...}, "excluded_provider_ids": [] }` and returns tenant ID, policy, governance policy, candidate capability descriptors, rankings, and exclusions. Governance update accepts `policy_id`, `allowed_provider_ids`, `denied_provider_ids`, `allowed_regions`, and `activate`; all list values must be unique.

## Developer diagnostics

Developer chat diagnostics can include request/timestamp fields; router used and attempts; selected/initial/final provider; backend/model; routing reason and task group; fallback path/count; circuit snapshot; capability descriptors considered; retry count; router/provider/gateway/total latency; safe HTTP/failure classifications; and V2 tenant/policy/governance references. Normal chat responses omit these internals.

Diagnostics do not include API keys, upstream error bodies, stack traces, raw provider responses, endpoints, or raw image data.

`http_status` is always the application API status. `upstream_status` is the last observed upstream error status, or null when unavailable; `upstream_http_status` remains a compatibility alias with the same meaning. Router/provider attempts use `upstream_status`, `failure_class`, and latency, never an overloaded `http_status`. Successful adapter calls currently do not expose their transport status, so their attempt status is null.

`router_fallback_used` identifies use of the fallback router. `provider_fallback_used` identifies a provider path with more than one candidate entry. `fallback_count` counts entries after the initial provider, including skipped candidates, and `fallback_path` explains each outcome. The legacy `fallback_used` aggregate remains available. Nullable diagnostic fields are serialized as null, not omitted. A successful response puts diagnostics under `data`; an error puts them beside `error`. Dashboard extraction supports both locations, preserves API request IDs, and JSON-encodes nested CSV fields. Missing fields are distinguished from explicit nulls.

`OVERALL_REQUEST_TIMEOUT` (default 60 seconds) bounds the engine lifecycle, including router calls and provider fallback. Provider attempts also have an async `REQUEST_TIMEOUT`; whichever deadline expires first wins. Global expiry cancels active work, stops fallback, and returns `504 DEADLINE_EXCEEDED` with stage `deadline`. No late answer is committed. Client timeouts should exceed the overall server deadline; the dashboard defaults to 90 seconds and accepts `api_timeout` and `developer_token` in its private Streamlit secrets configuration.

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
| 504 | `DEADLINE_EXCEEDED` | Overall engine deadline exhausted. |

Normal failures return only the public code/message. With developer mode, failure responses attach safe diagnostics and `failure_level`; upstream HTTP status is represented as a stable classification rather than an upstream body. Router timeouts are classified as `TIMEOUT`; each router tier has one bounded timeout budget covering its initial call and optional malformed-2xx repair. A provider 401/403 blocks that backend for the current request but does not trip its circuit.

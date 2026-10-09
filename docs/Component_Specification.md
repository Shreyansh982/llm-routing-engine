# Component specification

| Component | Responsibility | Boundary |
|---|---|---|
| `api/main.py` | Dependency assembly, HTTP validation/presentation, errors, developer-mode gate. | No classification/fallback policy. |
| `config/settings.py` | `.env` loading, backend/router validation, `PROVIDERS_JSON` parsing. | Router connection data never enters `RouterRequest`. |
| `routing/engine.py` | State, V2 candidate resolution, router ladder, provider loop, gateway, diagnostics. | Failed candidate never becomes `last_provider`. |
| `routers/http_router.py` | Provider-aware structured chat-completions transport with a per-tier timeout budget. | Google JSON schema, NVIDIA JSON object; only malformed 2xx gets one repair inside the same budget. |
| `registry/model_registry.py` | 12 task records; filters/orders candidates. | V2 ordering stays inside selected group. |
| `providers/dispatcher.py` | Circuit check/record and one task dispatch. | One adapter implementation maps to four backends. |
| `providers/adapter.py` | Text/image payload conversion and safe upstream error classification. | No upstream body/credential propagation. |
| `providers/circuit_breaker.py` | Monotonic backend `CLOSED`/`OPEN`/`HALF_OPEN` state. | Process-local, router/task shared. |
| `control_plane/` | V2 tenant policies, governance, rankings, audit references. | Produces candidate IDs/capabilities, not connection mappings. |

## Dashboard

`dashboard/app.py` supplies Chat, Routing Details, Analytics, Provider Health, Evaluation History, Settings, and Policy Studio. `dashboard/utils/api_client.py` is its only engine boundary.

Chat sends text plus one optional JPEG/JPG, PNG, or WebP upload; it base64-encodes only for the outgoing request. History/export keeps prompt/response and a boolean `has_image`, not image bytes, so stored image requests cannot be retried. Developer mode shows API-provided group, attempts, fallback, latency, and circuits. Analytics are browser-session calculations (counts, outcomes, latency, retries, trends, and developer-mode distributions), not persistent telemetry. The dashboard neither reproduces routing logic nor fabricates failure simulation.

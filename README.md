# Provider-Agnostic LLM Routing Engine

A FastAPI and Streamlit proof of concept that classifies each chat request into a task group, then deterministically executes a resilient, configuration-defined model fallback chain across cloud backends.

## Project snapshot

Static repository facts, verified from the referenced configuration and source files. They are not live-provider measurements.

| Area | Verified value | Source |
|---|---|---|
| Automated tests | 105 `test_*` functions | [`tests/`](tests/) |
| Routing evaluation | 15 fixed prompts; deterministic test-double acceptance threshold: at least 80% capability-strength matching | [`tests/test_routing_evaluation.py`](tests/test_routing_evaluation.py) |
| Configured backends | 4 | [`.env.example`](.env.example) |
| Router tiers | 2 (primary and fallback) | [`.env.example`](.env.example) |
| Primary router | `google` / `gemini-3.8-flash` | [`.env.example`](.env.example) |
| Fallback router | `nvidia` / `nvidia/nemotron-3.5-lightning-30b-a3b` | [`.env.example`](.env.example) |
| Explicit retry limit | 3 | [`config/settings.py`](config/settings.py) |
| Router tier time budget | 10 seconds | [`config/settings.py`](config/settings.py) |
| Router temperature / output cap | 0 / 128 tokens | [`config/settings.py`](config/settings.py) |
| Declared API endpoints | 14 | [`api/main.py`](api/main.py) |

The evaluation threshold exercises local selection logic through a deterministic fake router; it is not real Router-LLM routing accuracy or a live-model performance result. The dashboard calculates latency, retry, and fallback metrics only from interactions captured in its current browser session.

## Quick start

Prerequisites: Python 3.13+ and [uv](https://docs.astral.sh/uv/). There is no Node.js, Docker, database, or separately deployed service requirement.

```bash
copy .env.example .env
uv sync
uv run uvicorn api.main:app --reload
```

Add valid credentials for all four backends to the private `.env` before starting. In another terminal, start the optional dashboard:

```bash
uv run streamlit run dashboard/app.py
```

The API starts at `http://127.0.0.1:8000`; Swagger UI is at `http://127.0.0.1:8000/docs`. The dashboard defaults to that API URL. Run the default test suite with `uv run pytest -q`.

## What it does and why

Applications should not be coupled to one model/provider: providers can fail, free or prototype quotas can be rate-limited, model catalogs change, and different workloads need different configured capabilities. This POC separates two concerns:

- The **router** classifies a request into a task group.
- The **execution layer** selects and invokes concrete configured candidates for that group.

It provides provider/model abstraction, task-group classification, deterministic task-provider fallback, a distinct router fallback, backend health circuits, explicit conversation retry ownership, optional image understanding, and additive V2 policy/governance behavior. It is a POC, not a promise of unlimited provider access or production capacity.

## Current architecture

```text
Client / Streamlit dashboard
          |
          v
       FastAPI
          |
          +--> conversation state
          +--> V2 tenant governance and policy ranking (V2 only)
          |
          v
Google primary router ---- failure/invalid decision ----> NVIDIA fallback router
          |                                                     |
          +-------------------- task-group decision -----------+
                                      |
                                      v
                    Registry candidate filtering and ordering
                                      |
                                      v
                 Circuit-aware dispatcher (one call per candidate)
                                      |
                                      v
                 Shared OpenAI-compatible provider adapter
                                      |
                                      v
                   Response gateway -> API response / diagnostics
```

FastAPI validates requests and presents versioned contracts. `RoutingEngine` owns state, router failover, registry-candidate selection, and the sequential provider loop. `ModelRegistry` owns task-model metadata and priority. `ProviderDispatcher` owns circuit checks and dispatches through one shared OpenAI-compatible adapter implementation for four backend mappings. The response gateway scrubs configured assistant self-identification patterns while preserving code, formatting, and legitimate model references. The Streamlit dashboard calls FastAPI only.

### Task groups

The router can choose exactly one group for an `ANSWER`:

- `general` — configured for general Q&A, summaries, chat, and ordinary analysis.
- `coding_reasoning` — configured for coding, debugging, and involved reasoning.
- `creative` — configured for writing, rewriting, storytelling, captions, and ideation.
- `multimodal` — required whenever an image is supplied; its candidates accept text plus image input.

The router receives request text, image presence, conversation retry context, and available group names. It does **not** receive provider IDs, backend/model names, priorities, endpoints, credentials, or fallback chains. V2 governance filters candidates before classification; its policy ranking only orders candidates inside the selected group.

## Configured model topology

There are 14 active model configurations: two routers and 12 task models. The exact source is [`.env.example`](.env.example).

**Routers:** primary `google / gemini-3.8-flash`; fallback `nvidia / nvidia/nemotron-3.5-lightning-30b-a3b`.

| ID | Group | Role | Backend | Model | Priority | Input | Output |
|---|---|---|---|---|---:|---|---|
| `primary_router` | — | primary router | Google | `gemini-3.8-flash` | — | router request | structured decision |
| `fallback_router` | — | fallback router | NVIDIA | `nvidia/nemotron-3.5-lightning-30b-a3b` | — | router request | structured decision |
| `g1_google_flash_lite` | general | task candidate | Google | `gemini-3.5-flash-lite` | 1 | text | text |
| `g2_openrouter_qwen` | general | task candidate | OpenRouter | `qwen/qwen3.8-27b` | 2 | text | text |
| `g3_groq_oss20b` | general | task candidate | Groq | `openai/gpt-oss-20b` | 3 | text | text |
| `g4_nvidia_deepseek` | general | task candidate | NVIDIA | `deepseek-ai/deepseek-v4.1-flash` | 4 | text | text |
| `c1_groq_oss120b` | coding_reasoning | task candidate | Groq | `openai/gpt-oss-120b` | 1 | text | text |
| `c2_nvidia_kimi` | coding_reasoning | task candidate | NVIDIA | `moonshotai/kimi-k3` | 2 | text | text |
| `c3_openrouter_north` | coding_reasoning | task candidate | OpenRouter | `cohere/north-mini-code:free` | 3 | text | text |
| `c4_openrouter_laguna` | coding_reasoning | task candidate | OpenRouter | `poolside/laguna-s-2.1:free` | 4 | text | text |
| `w1_openrouter_gemma31b` | creative | task candidate | OpenRouter | `google/gemma-4-31b-it:free` | 1 | text | text |
| `w2_google_flash` | creative | task candidate | Google | `gemini-3.6-flash` | 2 | text | text |
| `m1_nvidia_nemotron_omni` | multimodal | task candidate | NVIDIA | `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` | 1 | text + image | text |
| `m2_openrouter_gemma26b` | multimodal | task candidate | OpenRouter | `google/gemma-4-26b-a4b-it:free` | 2 | text + image | text |

```text
General:          G1 -> G2 -> G3 -> G4
Coding/Reasoning: C1 -> C2 -> C3 -> C4
Writing:          W1 -> W2
Multimodal:       M1 -> M2
```

Priority defines V1 order. For V2, governance removes ineligible candidates first and a selected policy can order the remaining candidates; it cannot move one between task groups.

### Backend roles

| Backend | Configured role | Task-model use | Operational note |
|---|---|---|---|
| Google AI Studio / Gemini | primary router and task backend | G1, W2 | Its router circuit is shared with task calls. |
| NVIDIA NIM | fallback router and task backend | G4, C2, M1 | Its router circuit is shared with task calls. |
| OpenRouter | task backend | G2, C3, C4, W1, M2 | Some configured IDs use `:free`; availability and limits remain provider-controlled. |
| Groq | task backend | G3, C1 | Participates only in task fallback in this configuration. |

All four use cloud endpoints and operator-supplied keys. The repository does not model quota remaining, cost, or a provider promise of unrestricted free access.

`g2_openrouter_qwen` now uses OpenRouter's paid `qwen/qwen3.8-27b` slug. The provider catalog omits the former `:free` variant and its chat API explicitly rejects that variant as unavailable, so operators should review current OpenRouter pricing and quota before sending traffic.

## Routing, fallback, and circuits

The Google router gets one structured request. A transport or HTTP failure is not retried against it; the engine immediately tries the NVIDIA Nemotron fallback. A 2xx response with malformed JSON/schema gets at most one repair request. The initial request and possible repair share one `ROUTER_TIMEOUT` budget (10 seconds by default) per tier, so the two-tier ladder is bounded to roughly 20 seconds plus local handoff overhead. If both tiers fail, FastAPI returns `503 ROUTER_FAILURE`.

Router payloads follow each provider's supported contract. Google uses strict JSON-schema output with `reasoning_effort=low`; NVIDIA Nemotron uses JSON-object output with thinking disabled. Both use temperature `0` and a 128-token output cap.

For `ANSWER`, the registry filters enabled, eligible, modality-compatible candidates and invokes them in order. A task candidate gets one attempt; the engine advances after failure and returns `503 PROVIDER_UNAVAILABLE` only after the selected group is exhausted. Explicit `retry: true` is separate: it excludes the prior successful provider and increments the conversation attempt count up to `MAX_RETRIES` (default `3`). This avoids retry multiplication across layers.

There is one process-local circuit per backend: `google`, `nvidia`, `openrouter`, and `groq`. Router and task calls share it, so a circuit can skip multiple models on one backend.

```text
CLOSED -- 3 qualifying consecutive failures --> OPEN -- 30 seconds --> HALF_OPEN
  ^                                                                    |
  +---------------------------- successful probe ---------------------+
```

Defaults are threshold `3`, open duration `30` seconds, and `1` half-open probe. HTTP 5xx (unless model-specific), timeouts, and network errors are backend transient failures. A 429 is scoped to the attempted model: quota exhaustion alone does not prove a backend outage. The next eligible model remains available. Authentication/authorization, malformed responses, unsupported format, and model-specific failures do not open a circuit. A 401/403 blocks its backend only for the current request. Cancelled or unexpectedly failed half-open probes release their slot; probe timeouts reopen the circuit. Circuit state resets on process restart; authorized developer health reads only report it.

The engine enforces `OVERALL_REQUEST_TIMEOUT` across routing and provider fallback, returning 504 when exhausted. Each provider attempt also has an async timeout. Router `RETRY` is accepted only on explicit retry requests and consumes the same turn budget; new messages reset retry state and update `original_query`. Catalog health checks fetch once per backend per operation and indicate catalog presence, not guaranteed successful inference.

## Images and multimodal requests

One optional base64 JPEG, PNG, or WebP image can accompany a request. Image-bearing requests expose only `multimodal` to the router and use M1 -> M2. The adapter turns the image into an OpenAI-compatible `image_url` data part only while building the upstream request.

Not supported: multiple images, image URLs, audio, video, image generation, or a claim that every underlying-model capability is exposed. Image bytes are not retained in conversation state, diagnostics, dashboard history, or dashboard exports; the dashboard keeps only a `has_image` flag and cannot retry the stored image request.

## Configuration

Settings load from `.env`; begin by copying `.env.example`. Keep keys out of version control.

| Variable | Required | Purpose | Default/example |
|---|---:|---|---|
| `GOOGLE_API_KEY`, `NVIDIA_API_KEY`, `OPENROUTER_API_KEY`, `GROQ_API_KEY` | yes | backend bearer credentials | `your_key_here` |
| `GOOGLE_ENDPOINT`, `NVIDIA_ENDPOINT`, `OPENROUTER_ENDPOINT`, `GROQ_ENDPOINT` | yes | backend chat-completions URLs | values in `.env.example` |
| `PRIMARY_ROUTER_BACKEND` / `PRIMARY_ROUTER_MODEL` | yes | primary classifier | `google` / `gemini-3.8-flash` |
| `FALLBACK_ROUTER_BACKEND` / `FALLBACK_ROUTER_MODEL` | yes | fallback classifier | `nvidia` / `nvidia/nemotron-3.5-lightning-30b-a3b` |
| `PROVIDERS_JSON` | yes | 12 task-model registry records | provided JSON array |
| `MAX_RETRIES` | no | maximum explicit conversation retries | `3` |
| `REQUEST_TIMEOUT` | no | timeout for each task-model attempt, in seconds | `20` |
| `OVERALL_REQUEST_TIMEOUT` | no | total engine deadline, in seconds | `60` |
| `MAX_IMAGE_BYTES` | no | maximum decoded image size | `5242880` |
| `DEVELOPER_MODE_ENABLED` | no | operator opt-in for diagnostics | `false` |
| `DEVELOPER_TOKEN` | no | optional token required via `X-Developer-Token` | unset |
| `ROUTER_TIMEOUT` | no | whole-request budget for each router tier, including repair | `10` |
| `ROUTER_TEMPERATURE` | no | router temperature | `0` |
| `ROUTER_MAX_TOKENS` | no | router output cap | `128` |
| `ROUTER_REASONING_EFFORT` | no | Google router reasoning setting; omitted from NVIDIA | `low` |
| `CIRCUIT_FAILURE_THRESHOLD` | no | failures before open | `3` |
| `CIRCUIT_OPEN_SECONDS` | no | circuit cooldown | `30` |
| `CIRCUIT_HALF_OPEN_PROBES` | no | recovery probes | `1` |

For compatibility, settings also accept legacy `GOOGLE_BASE_URL`, `NVIDIA_BASE_URL`, `OPENROUTER_BASE_URL`, and `GROQ_BASE_URL` (with `/chat/completions` appended) plus the old `OPENROOUTER_API_KEY` spelling. Use endpoint variables and `OPENROUTER_API_KEY` for new configuration. `RUN_CLOUD_INTEGRATION=1` is test-only, not an application setting.

## API examples

Application success and controlled application-error routes return `{ "success": true, "data": ... }` or `{ "success": false, "error": ... }`. V1 base path is `/api/v1`; V2 is `/api/v2`.

```bash
# General, coding, and creative text requests
curl -X POST http://127.0.0.1:8000/api/v1/chat -H "Content-Type: application/json" -d "{\"conversation_id\":\"general-1\",\"message\":\"Summarize these notes.\"}"
curl -X POST http://127.0.0.1:8000/api/v1/chat -H "Content-Type: application/json" -d "{\"conversation_id\":\"code-1\",\"message\":\"Write a Python function that reverses a list.\"}"
curl -X POST http://127.0.0.1:8000/api/v1/chat -H "Content-Type: application/json" -d "{\"conversation_id\":\"write-1\",\"message\":\"Draft a warm thank-you note.\"}"
```

```json
// POST /api/v1/chat image request; data_base64 is deliberately abbreviated.
{
  "conversation_id": "image-1",
  "message": "Describe this image.",
  "image": {"media_type": "image/png", "data_base64": "iVBORw0KGgoAAA..."},
  "retry": false
}
```

`GET /api/v1/health` provides liveness. `GET /api/v1/router/health` reports router health plus circuit snapshots, and `GET /api/v1/providers/health` reports task-provider health plus circuits. See [API contracts](docs/API_Contracts.md) for every V1/V2 route and schema.

## Evaluation, dashboard, and status

The dashboard provides session-local records: total/success/failure counts, average/median request latency, retry and fallback rate when diagnostics exist, trends, and developer-mode provider/router distributions. It exports session records as CSV/JSON. It does not persist metrics or establish a scientific provider benchmark.

The local routing-evaluation test uses a deterministic fake router and 15 prompts; it checks at least 80% capability-strength matching. That is a regression check, not a real-model quality, accuracy, or latency claim.

Implemented now: V1/V2 contracts, two-tier structured routers, four groups, 12 task candidates, four backends, sequential fallback, shared circuits, opt-in diagnostics, image input, V2 policy/governance, and an API-only dashboard.

## Security, limitations, and risks

API keys are `SecretStr` settings and excluded from configuration diagnostics. Normal chat output omits routing internals. Provider errors are converted to safe classifications instead of upstream bodies. Image bytes are not stored in engine/dashboard artifacts. Developer configuration endpoints intentionally show non-secret endpoint/model metadata, so developer mode should be limited to trusted operators.

Current limitations: process-local conversation/policy/audit/circuit state, one image, no rich media generation/audio/video, no durable observability, and opt-in live cloud tests. Provider availability, catalogs, structured-output behavior, quotas, and limits are external risks; OpenRouter free-model IDs and cloud endpoints can change. Multi-worker deployments would not share circuit state.

See the [documentation index](docs/README.md) and [roadmap](docs/Future_Roadmap.md).

# Provider-Agnostic LLM Routing Engine

This repository implements the documented provider-agnostic LLM Routing Engine POC. It is a
FastAPI middleware that lets a local Router LLM choose among abstract provider IDs using
configuration-defined capability descriptors. The engine owns retry bookkeeping and the
finite failure ladder; it never contains provider-selection intelligence.

## Quick start

```bash
cp .env.example .env
uv sync
uv run uvicorn api.main:app --reload
```

The API is versioned under `/api/v1`. Its POC endpoints are `POST /chat`, `GET
/conversation/{id}`, `GET /providers`, `GET /health`, `GET /router/health`, and `GET
/providers/health`.

```bash
uv run pytest
```

## Configuration

All runtime values are external configuration: router URLs/models, request timeout, retry
limit, provider mapping, enabled state, and vendor-neutral capability descriptors. Copy
`.env.example` and replace the endpoint/model values with local services.

Router decoding is configured independently of routing decisions: `ROUTER_TEMPERATURE=0`,
`ROUTER_MAX_TOKENS=96`, and `ROUTER_REASONING_EFFORT=none` provide deterministic,
short structured classifications for the OpenRouter Router. The OpenRouter-only reasoning
setting is not sent to the Groq fallback.

The included Router and Provider adapters use configured OpenRouter and Groq
chat-completions endpoints with `stream: false`. Router calls request strict JSON-schema
output; provider calls receive only the prompt and an identity-hiding system instruction.
All backend, model, endpoint, timeout, and credential lookup details stay in configuration,
while the Router receives only provider IDs and abstract capability descriptors.

`PROVIDERS_JSON` is a JSON array. Provider endpoint/model mapping remains private in the
Model Registry; the Router receives only IDs and `strengths`, `speed_tier`, and
`context_size` capability descriptors.

## Behaviour

- A client retry is explicit: `POST /api/v1/chat` with `retry: true` excludes the prior
  provider and increments the attempt count.
- Retry stops before another Router call once the configured maximum is reached or the
  enabled provider pool is exhausted.
- Primary Router failures demote to Fallback Router, then the non-AI deterministic default
  router, then a controlled 503.
- Provider replies are guarded by an adapter identity-hiding prompt and a best-effort
  Response Gateway filter.

The implementation and acceptance criteria are defined in [docs](docs/README.md).

## Evaluation dashboard

Start the FastAPI service first, then run the evaluation-only Streamlit dashboard in a
second terminal. It consumes only the versioned FastAPI endpoints and does not contact cloud
providers directly.

```bash
uv run streamlit run dashboard/app.py
```

Use Developer Mode to send `X-Developer-Mode: true` with dashboard API calls. The backend then
returns request diagnostics (router tier, selected provider, backend/model, reason,
capabilities, retry/fallback state, request ID, timestamp, and latency breakdown) and safe
read-only provider/router configuration metadata. API keys are never returned. Normal API
calls omit diagnostics entirely. Evaluation History is maintained in the browser session and
can be exported as CSV or JSON.

Developer diagnostics are also returned for failed requests. They contain all safe metadata
captured before failure plus the terminal `failure_stage` (`primary_router`, `fallback_router`,
`dispatcher`, `provider`, `gateway`, `validator`, or `default_router`), a stable
`failure_reason`, upstream HTTP status when available, configured provider backend/model,
timestamps, and a safe provider-error message where applicable. The normal-user error contract
remains unchanged.

Successful Developer Mode responses report `failure_stage: "none"` and
`failure_reason: "NONE"`; `UNKNOWN` is reserved for an unclassified failure.

`provider_a` uses OpenRouter's `openai/gpt-oss-20b:free`: a zero-priced OpenRouter catalog
model chosen to replace the intermittently billable DeepSeek-R1 mapping. It preserves the
reasoning/coding capability descriptor without duplicating the Qwen router, Groq Llama, or
Gemma provider models.

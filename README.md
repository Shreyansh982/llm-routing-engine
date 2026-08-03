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

The documentation does not mandate a particular local LLM runtime protocol. The included
HTTP router and provider adapters consequently use the common OpenAI-compatible
chat-completions envelope. Router calls request strict JSON-schema output; provider calls
receive only the prompt and an identity-hiding system instruction. A different local or
vendor protocol is added by implementing the documented `BaseRouter` or `BaseProvider`
interface, without changing the Routing Engine.

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

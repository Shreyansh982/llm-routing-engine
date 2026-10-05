# System architecture

## Request lifecycle

```text
HTTP request
  -> FastAPI/Pydantic validation
  -> conversation state lookup or creation
  -> V2 tenant governance + policy candidate resolution (V2 only)
  -> RouterRequest: text context, retry context, image flag, available groups
  -> Google primary router -> NVIDIA fallback router on failure/invalid decision
  -> validated task-group decision
  -> registry candidate filtering/order
  -> circuit-aware provider dispatch and sequential fallback
  -> response gateway -> state update, envelope, optional diagnostics
```

`api/main.py` assembles dependencies and maps controlled errors. `routing/engine.py` orchestrates. `state/manager.py` owns conversations; `control_plane/` resolves V2 candidates; `registry/model_registry.py` owns task metadata; `providers/` owns transport/circuits; `gateway/response_gateway.py` processes successful output.

## Router vs execution

The router classifies the task; the execution layer decides which model handles it. `RouterRequest` contains original/latest text, previous response, attempt/max attempts, `has_image`, and available task groups—not provider IDs, model IDs, priorities, endpoints, credentials, or fallback chains. A router `ANSWER` requires one group; `RETRY`, `CLARIFY`, and `STOP` cannot have one.

The registry filters candidates by enabled state, V2 eligibility, group, modality, and exclusions. V1 sorts by priority/ID. In V2, pre-classification policy order is preserved only within the selected group.

## Fallback and retry ownership

```text
Google `gemini-3.8-flash` primary router
  -> failure / invalid decision -> NVIDIA `z-ai/glm-5-3-flash`
  -> failure -> controlled `503 ROUTER_FAILURE`
```

Transport failure is not retried against the same router. A malformed 2xx structured result gets one repair request. A 401/403 blocks that backend for this request without opening its circuit.

Task candidates are sequential and receive at most one call per request. There is no same-model/adapter retry; the engine advances or returns `503 PROVIDER_UNAVAILABLE` after exhaustion. `MAX_RETRIES` instead bounds explicit conversation retry (`retry: true`) and router `RETRY`: the prior successful provider is excluded and the attempt count increments. This prevents retry multiplication.

## Backend circuits

One `BackendCircuits` object holds circuits for `google`, `nvidia`, `openrouter`, and `groq`, shared by router/task calls.

```text
CLOSED -- 3 transient failures --> OPEN -- 30 seconds --> HALF_OPEN
  ^                                                        |
  +-------------------- successful probe -----------------+
                     failed transient probe -> OPEN
```

Defaults are threshold 3, cooldown 30 seconds, one half-open probe. 429/500/502/503/504 (unless model-specific), timeout, and network failure are transient. 400/401/403, schema/format, and model-specific failure are not. An open backend skips all its models. Health snapshots do not mutate state; state resets at process restart.

## Multimodal flow

```text
validated JPEG / PNG / WebP base64 image
  -> has_image=true -> router must choose multimodal
  -> M1 then M2 candidates -> one OpenAI-compatible image data URL
```

Only one image object is accepted. Raw image bytes are not stored in conversation state, diagnostics, dashboard history, or exports.

# Project overview

Provider-Agnostic LLM Routing Engine is a proof of concept that separates LLM task classification from concrete provider execution. It classifies a chat request into a task group, then follows a configuration-defined candidate chain instead of binding application code to one model/provider.

## Current status

The implementation has two structured-output routers, 12 task models across Google, NVIDIA, OpenRouter, and Groq, four task groups, V1/V2 APIs, backend-level circuits, optional single-image requests, and a Streamlit dashboard that consumes FastAPI only. V1 uses registry priority. V2 adds tenant policy and governance filtering/ranking without handing model mappings to the router.

Its key boundaries are: **router** (action/group only), **registry** (private configured model metadata), **execution** (one-call-per-candidate fallback), **circuit** (shared backend health), and **gateway** (successful output normalization/masking).

## Terms

| Term | Meaning |
|---|---|
| task group | `general`, `coding_reasoning`, `creative`, or `multimodal`. |
| router | Structured classifier; it never selects a concrete model. |
| task provider / candidate | A configured task-model record in `PROVIDERS_JSON`. |
| backend | `google`, `nvidia`, `openrouter`, or `groq`, each with a circuit. |
| fallback | Primary-to-fallback router or one task candidate to the next. |
| circuit | Process-local `CLOSED`, `OPEN`, or `HALF_OPEN` backend state. |

## Boundaries and limitations

Conversation, policy, audit, and circuit state are in memory. There is no distributed state store, database catalog, scheduler, quota manager, durable telemetry, audio/video, image generation, multiple images, or automatic model discovery. Provider quotas, pricing, availability, and compatibility are external. Dashboard analytics are session-local observations, not a benchmark; default tests use fakes/mocks and the live cloud test is opt-in.

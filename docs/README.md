# Documentation index

This set describes the implementation in this repository. [`.env.example`](../.env.example), route definitions, schemas, and automated tests are the runtime sources of truth.

- [Project overview](Project_Overview.md) — scope, status, limits, and terms.
- [System architecture](System_Architecture.md) — request lifecycle, fallback, circuits, and images.
- [Component specification](Component_Specification.md) — module and dashboard responsibilities.
- [API contracts](API_Contracts.md) — V1/V2 routes, schemas, diagnostics, and errors.
- [Testing strategy](Testing_Strategy.md) — verified result, test scope, cloud opt-in, and evaluation.
- [Future roadmap](Future_Roadmap.md) — completed, near-term, future, and out-of-scope work.
- [Historical implementation plan](Implementation_Plan.md) — superseded record only.

Start with the repository [README](../README.md) for setup and the configured model topology.

The current router ladder is Google `gemini-3.8-flash` followed by NVIDIA `nvidia/nemotron-3.5-lightning-30b-a3b`, with a separate per-tier router timeout.

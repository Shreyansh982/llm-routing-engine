# Future roadmap

No dates are committed. The list distinguishes current implementation from possible next work.

## Completed

- Configuration-driven two-router/twelve-task-model topology across four backends.
- Router/execution separation, sequential fallback, bounded conversation retry, and shared backend circuits.
- Provider-aware Google-to-NVIDIA Nemotron routing with independent per-tier timeout budgets and startup catalog verification.
- Single-image input with non-retention, safe diagnostics, V2 policy/governance, API-only dashboard, and an opt-in cloud test.

## Near-term

- Recurring operational checks for configured live router structured-output compatibility and model availability.
- A reproducible evaluation dataset beyond the small deterministic regression set.
- Operator-facing detection for deprecation, quota, and rate-limit conditions.

## Future

- Durable multi-worker conversation/policy/audit storage and distributed circuit state.
- Persistent health/telemetry plus quota-, cost-, latency-, or capacity-aware ranking.
- Model availability discovery, production observability, and richer multimodal contracts after privacy/retention review.

## Out of scope unless requirements change

- Guaranteed free/prototype provider capacity.
- Image generation, audio/video, multiple images, or URL fetching under the current contract.
- A second catalog, provider-SDK proliferation, or separate group-specific fallback engines.

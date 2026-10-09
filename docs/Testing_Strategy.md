# Testing strategy

## Verified baseline

Before audit hardening on 2026-10-06, the repository virtual environment reported **98 collected tests** in **14 `test_*.py` files**, then **97 passed, 1 skipped**, and **93% coverage** (excluding dashboard code). The completed hardening verification is recorded below.

```powershell
& .\.venv\Scripts\python.exe -m pytest --collect-only -q
& .\.venv\Scripts\python.exe -m pytest -q
```

Normal project commands are:

```bash
uv run pytest -q
uv run pytest tests/test_circuit_breaker.py -q
```

The run emitted one Starlette/httpx deprecation warning. These are a validation snapshot, not a coverage target or performance claim.

## Coverage scope

The default suite uses fakes/mocks and covers V1/V2 contracts, configuration topology, provider-specific router payloads, per-tier timeout ownership, immediate 503/timeout failover, the single malformed-2xx repair, bounded dual-router failure, all four task chains, one task attempt per candidate, request-local auth suppression, images and non-retention, circuit transitions with an injected fake monotonic clock (no sleeps), gateway masking, adapter sanitization, dashboard records, health, and candidate ordering.

Startup catalog tests verify provider-aware ID resolution, exact OpenRouter `:free` handling, backend/model failure reasons, and one `/models` request per backend even when that backend has several configured models.

The local routing-evaluation test has 15 fixed prompts and a deterministic fake router; it requires at least 80% capability-strength matching. It is regression coverage for local logic, not a live-model accuracy benchmark.

## Opt-in cloud test

Default tests consume no cloud quota. The lone `cloud` test requires valid configured Google, NVIDIA, OpenRouter, and Groq keys, and is intentionally skipped unless enabled:

```powershell
$env:RUN_CLOUD_INTEGRATION = "1"
& .\.venv\Scripts\python.exe -m pytest tests/test_cloud_integration.py -q
```

It validates both the Google primary and NVIDIA Nemotron fallback routers, then makes one task-model call per backend. It is opt-in because it can consume quota and depends on live model availability/rate limits; live 402/429 and transient backend failures are reported as operational conditions rather than deterministic code failures. Contract and payload errors still fail. It does not make an image call.

## Audit hardening verification — 2026-10-06

All 14 findings were addressed; none were intentionally deferred. The four backends, two-router ladder, task-group-only contract, configured task candidates, shared backend circuits, tenant isolation, and V2 governance ordering remain in place. Startup catalog matching and malformed-success repair tests remain enabled.

| Finding | Change | Regression evidence in `tests/test_audit_hardening.py` |
|---|---|---|
| F-01 | Shared success/error diagnostic extraction. | Actual API → HTTP client → chat record → CSV, for success and failure. |
| F-02 | Targeted self-identification scrubbing; no global replacements or whitespace cleanup. | Self-identification, legitimate references, fenced code, indentation, tables, and technical metadata. |
| F-03 | Explicit router retry flag and per-turn reset. | Three-turn recovery after repeated RETRY; new-turn RETRY rejected; previous answer retained. |
| F-04 | Configurable overall async deadline plus provider attempt timeout. | Slow provider cancellation, skipped remaining candidates, no late answer commit, router deadline API response, and bounded provider fallback. |
| F-05 | Quota errors do not count as backend outages. | 429 on one model leaves another callable on each of the four backends. Existing backend 5xx/timeouts/network tests remain. |
| F-06 | Standard framework error envelopes with correct HTTP semantics. | Body/tenant validation, unknown routes, unsupported methods, and preserved Allow header. |
| F-07 | One request ID and captured engine context for unexpected errors. | Registry failure retains the policy-audit ID and routing attempts; outside-engine failures are sanitized and logged by ID. |
| F-08 | Separate API and upstream status; structured failure classes. | API 503 with upstream 429, deadline classification, null upstream status, and attempt metadata. |
| F-09 | Preserve nulls/IDs; use diagnostic retry count; distinguish fallback types; JSON CSV serialization. | Complete cross-layer export, absent versus null, no generated replacement ID, and dashboard analytics smoke test. |
| F-10 | Release interrupted half-open slots; reopen on probe timeout. | Router and provider probes tested for cancellation, unexpected exception, and timeout. |
| F-11 | Diagnostics disabled by default, with operator enablement and optional token. | Disabled, missing-token, wrong-token, authorized, error-response, health, and dashboard-token paths. |
| F-12 | Deterministic CLARIFY/STOP user messages. | Internal reasons remain only in diagnostics; provider dispatch is skipped. |
| F-13 | Reuse backend catalog verification for health. | One fetch per backend per health operation, including direct dispatcher health; startup verification suite retained. |
| F-14 | Configurable image limit, one decode, MIME signature checks, retry reattachment. | Oversize rejected before decoding; malformed/mislabeled input; valid image forwarding; image retries, including images added during retry; no payload retention. |

Added **53 parametrized regression cases** in `tests/test_audit_hardening.py`. Existing tests were updated only for intentionally changed contracts: diagnostics enablement/null fields, API versus upstream status, whitespace preservation, valid image fixtures, explicit router retry, and 429 scope. The former backend-wide 429 expectation is replaced by four backend-specific quota-isolation cases.

Final command:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q --cov-report=term --cov-report=json:.pytest_cache/audit-coverage.json
```

Result: **149 passed, 1 skipped**. The skip is the opt-in live-cloud test. One existing Starlette/httpx deprecation warning remains. Coverage is **83% overall**, now including all dashboard modules; backend-only coverage is approximately **94%**, dashboard coverage **52%**, gateway **100%**, routing engine **95%**, and dashboard records **92%**. The expanded scope makes the overall percentage incomparable with the earlier backend-only 93%. The generated detailed report is `.pytest_cache/audit-coverage.json`. `git diff --check` passes.

Files changed by this hardening work (pre-existing working-tree changes were preserved):

- Runtime: `api/main.py`, `config/settings.py`, `routing/engine.py`, `routers/http_router.py`, `schemas/models.py`, `validation/decision_validator.py`, `gateway/response_gateway.py`, `providers/adapter.py`, `providers/circuit_breaker.py`, `providers/dispatcher.py`, `providers/model_catalog.py`.
- Dashboard: `dashboard/app.py`, `dashboard/components/common.py`, `dashboard/pages/analytics.py`, `dashboard/pages/chat.py`, `dashboard/pages/provider_health.py`, `dashboard/pages/routing_details.py`, `dashboard/utils/api_client.py`, `dashboard/utils/records.py`.
- Tests: `tests/test_audit_hardening.py` (new), `tests/conftest.py`, `tests/test_api.py`, `tests/test_circuit_breaker.py`, `tests/test_dashboard_records.py`, `tests/test_gateway_and_adapters.py`, `tests/test_migration_routing.py`, `tests/test_routing_engine.py`.
- Configuration/docs: `.env.example`, `.gitignore`, `pyproject.toml`, `README.md`, `docs/API_Contracts.md`, `docs/Project_Overview.md`, `docs/System_Architecture.md`, `docs/Testing_Strategy.md`.

Consumer changes are detailed in [API contracts](API_Contracts.md): `http_status` now consistently means the application status; use `upstream_status` for provider/router status (`upstream_http_status` remains an alias). Diagnostic nulls are explicit. New fields include `failure_class`, `router_fallback_used`, and `provider_fallback_used`. Router requests carry `retry`; conversation state keeps only an image-presence marker. Global deadline failures return HTTP 504. Developer diagnostics and circuit snapshots require operator enablement and, when configured, the developer token. Dashboard CSV nested fields are JSON, including explicit nulls.

Remaining limits: live provider inference was not exercised; catalog health is not an inference guarantee. Image validation checks lightweight signatures/headers rather than fully decoding image formats. Async deadlines rely on cancellation-cooperative adapters, as the existing HTTPX transports are; synchronous event-loop blocking cannot be preempted by an async timeout. These changes retain the POC's process-local state/circuits and existing tenant-header trust model. UI coverage is partial, although the modified chat/export boundary and dashboard startup/analytics paths are tested.

# Testing strategy

## Verified baseline

On 2026-10-05, the repository virtual environment reported **83 collected tests** in **12 `test_*.py` files**, then **82 passed, 1 skipped**, and **92% coverage**:

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

The default suite uses fakes/mocks and covers V1/V2 contracts, configuration topology, router JSON-schema/failover/repair, all four chains, one task attempt per candidate, request-local auth suppression, images and non-retention, circuit transitions with an injected fake monotonic clock (no sleeps), gateway masking, adapter sanitization, dashboard records, health, and candidate ordering.

The local routing-evaluation test has 15 fixed prompts and a deterministic fake router; it requires at least 80% capability-strength matching. It is regression coverage for local logic, not a live-model accuracy benchmark.

## Opt-in cloud test

Default tests consume no cloud quota. The lone `cloud` test requires valid configured Google, NVIDIA, OpenRouter, and Groq keys, and is intentionally skipped unless enabled:

```powershell
$env:RUN_CLOUD_INTEGRATION = "1"
& .\.venv\Scripts\python.exe -m pytest tests/test_cloud_integration.py -q
```

It validates the primary router, makes one task call for every registered provider, and performs one end-to-end request. It is opt-in because it can consume quota and depends on live model availability/rate limits. It does not make an image call.

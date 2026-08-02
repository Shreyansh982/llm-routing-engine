# Testing Strategy

## Provider-Agnostic LLM Routing Engine

**Document Version:** 1.0

---

# Purpose

This document defines the testing strategy for the Provider-Agnostic LLM Routing Engine Proof of Concept (POC).

The objective of testing is to ensure that every component behaves correctly both independently and as part of the complete routing workflow.

The testing strategy focuses on:

- Functional correctness
- Reliability
- Fault tolerance
- Component isolation
- End-to-end workflow validation

---

# Testing Objectives

The testing process should verify that:

- Every component behaves as expected.
- Components communicate correctly.
- Routing decisions are executed successfully.
- Retry mechanisms function correctly.
- Fallback routing activates automatically.
- Provider abstraction remains independent of vendor implementations.
- Provider identities are masked on a best-effort basis (adapter system prompt + Gateway).
- Invalid states are handled gracefully.

---

# Testing Pyramid

```
                   Integration Tests
                         ▲
                         │
                   Component Tests
                         ▲
                         │
                     Unit Tests
```

The majority of tests should be Unit Tests.

---

# Test Categories

| Test Type | Purpose |
|-----------|---------|
| Unit Testing | Validate individual components |
| Component Testing | Validate interaction between related components |
| Integration Testing | Validate complete request lifecycle |
| Failure Testing | Validate error handling |
| Regression Testing | Ensure future changes do not break existing behavior |

---

# Unit Testing

Each component must be tested independently.

---

# API Layer

## Test Cases

- Valid request
- Missing request fields
- Invalid request format
- Empty message
- Invalid conversation ID

Expected Result

- Proper HTTP response
- Proper status codes

---

# Conversation State Manager

## Test Cases

Create conversation

Load conversation

Update conversation

Increment retry count

Exclude provider

Reset conversation

Retrieve unknown conversation

Expected Result

State updates correctly.

---

# Model Registry

## Test Cases

Register provider

Remove provider

Disable provider

Enable provider

Lookup provider

Lookup unknown provider

Expected Result

Registry always returns valid metadata.

---

# Primary Router

## Test Cases

Valid routing request

Invalid routing request

Malformed response

Unknown provider

Invalid action

Structured-output conformance (decision matches the required schema)

Capability-aware selection (given descriptors, a coding query prefers a coding-strong
provider)

Expected Result

Router returns a schema-conformant RouterDecision.

---

# Deterministic Default Router

## Test Cases

Selects first enabled, non-excluded provider

Skips disabled providers

Skips excluded providers

No eligible provider remains

Expected Result

Returns the correct deterministic provider, or signals "no provider available" so the engine
returns a controlled 503. Never invokes an AI model.

---

# Fallback Router

## Test Cases

Primary timeout

Primary unavailable

Primary invalid JSON

Primary invalid provider

Expected Result

Fallback router generates valid decision.

---

# Decision Validator

## Test Cases

Valid JSON

Malformed JSON

Invalid schema

Unknown provider

Disabled provider

Excluded provider

Invalid confidence

Invalid action

Expected Result

Invalid decisions rejected.

---

# Provider Dispatcher

## Test Cases

Dispatch Provider A

Dispatch Provider B

Dispatch Provider C

Unknown provider

Disabled provider

Expected Result

Correct adapter selected.

---

# Provider Adapter

## Test Cases

Successful generation

Provider timeout

Provider unavailable

Malformed provider response

Unexpected exception

Expected Result

Provider exceptions handled gracefully.

---

# Response Gateway

## Test Cases

Provider identity removal

Metadata removal

Formatting normalization

Already clean response

Empty response

Expected Result

Final response follows platform policy.

---

# Component Testing

Component tests verify interaction between related components.

---

## Routing Engine + Router

Verify

- Router request generation
- Router invocation
- Router decision parsing

---

## Routing Engine + Validator

Verify

- Invalid decisions rejected
- Valid decisions accepted

---

## Dispatcher + Registry

Verify

- Provider lookup
- Adapter resolution

---

## Dispatcher + Provider Adapter

Verify

- Correct provider invoked
- Response returned correctly

---

## Routing Engine + Response Gateway

Verify

- Every response passes through Gateway
- No provider identity leakage

---

# Integration Testing

Integration tests validate the complete workflow.

---

## Test 1

Normal Request

```
User

↓

API

↓

Routing Engine

↓

Router

↓

Provider

↓

Gateway

↓

User
```

Expected Result

Successful response.

---

## Test 2

Retry

```
User

↓

Provider A

↓

Explicit retry signal (retry: true)

↓

Provider A excluded, attempt_count++

↓

Provider B

↓

Gateway

↓

User
```

Expected Result

Retry succeeds deterministically (no satisfaction inference).

---

## Test 3

Fallback Router

```
Primary Router

↓

Failure

↓

Fallback Router

↓

Provider

↓

Gateway
```

Expected Result

Fallback router activates automatically.

---

## Test 4

Maximum Retry

Expected

```
Attempt 1

↓

Attempt 2

↓

Attempt 3

↓

STOP
```

No additional Router invocation.

---

## Test 5

Provider Disabled

Expected

Dispatcher rejects provider.

---

## Test 6

Provider Failure

Expected

Graceful failure.

---

## Test 7

Gateway (best-effort masking of a known identity string)

Provider returns

```
I am Claude.
```

Gateway returns

```
I am an AI assistant.
```

Note: this validates best-effort masking of *known* identity strings. The primary defense
(adapter system prompt) is validated separately at the adapter level.

---

## Test 8

Complete Failure Ladder

```
Primary Router  → invalid decision
        ↓
Fallback Router → invalid decision
        ↓
Deterministic Default Router → valid provider
        ↓
Gateway → User
```

Expected Result

The ladder escalates deterministically and terminates. If no provider is eligible at the
default tier, a controlled 503 (ROUTER_FAILURE) is returned.

---

## Test 9

Deterministic Retry

```
POST /chat (retry: true)
        ↓
previous provider excluded
        ↓
attempt_count++
        ↓
Router re-invoked with reduced provider set
```

Expected Result

Retry re-routes deterministically; no satisfaction inference occurs.

---

## Test 10

Provider Pool Exhaustion

Expected Result

When every enabled provider is excluded, the engine returns `STOP` without invoking the
Router. No infinite loop occurs.

---

# Failure Testing

The system must tolerate failures.

---

## Router Timeout

Expected

Fallback Router invoked.

---

## Router Invalid JSON

Expected

Fallback Router invoked.

---

## Provider Timeout

Expected

Controlled error returned.

---

## Unknown Provider

Expected

Validation failure.

---

## Invalid Router Decision

Expected

Rejected before Dispatcher.

---

## Invalid Conversation

Expected

Conversation not found.

---

## Maximum Retry Reached

Expected

STOP action returned.

---

# Regression Testing

Regression tests ensure future changes do not introduce unexpected behavior.

Every pull request should execute

- Unit Tests
- Component Tests
- Integration Tests

---

# Performance Testing

Although performance is outside the scope of the POC, basic measurements should be collected.

Metrics

- Router latency
- Provider latency
- Total request latency
- Gateway latency

These values should be logged for future optimization.

---

# Logging Verification

Verify logs contain

- Conversation ID
- Selected Provider ID
- Attempt Count
- Router Decision
- Retry Count
- Errors
- Latency

Logs must never expose

- API Keys
- Router Prompt
- Provider Secrets

---

# Router Routing-Quality Evaluation

Structural tests confirm the Router returns *valid* decisions; they do not confirm the
decisions are *good*. Because the Router is the single intelligent component, its routing
quality is evaluated explicitly against a small, fixed dataset.

## Evaluation Dataset

A small curated set of labeled cases (target: **15–20 cases** for the POC). Each case pairs a
representative query with the capability profile it should be routed to, given a fixed set of
provider capability descriptors.

```json
[
  {
    "query":"Write a Python function to reverse a linked list.",
    "expected_strength":"coding"
  },
  {
    "query":"Summarize this three-page article into five bullet points.",
    "expected_strength":"summarization"
  },
  {
    "query":"Write a short whimsical poem about autumn.",
    "expected_strength":"creative_writing"
  }
]
```

Labels reference **abstract capabilities** (e.g. `coding`, `summarization`,
`creative_writing`), never vendor names, preserving provider-agnosticism. The dataset is
hand-authored — no ML training, embeddings, or classifiers are involved.

## Acceptance Criteria

- **Top-1 capability match ≥ 80%** — for at least 80% of cases, the selected provider's
  descriptor includes the expected strength.
- **100% schema conformance** — every decision is valid structured output.
- **No excluded/disabled provider is ever selected** across the dataset.

A run below the top-1 threshold is treated as a routing-prompt quality regression to be
addressed (e.g. by refining the Router prompt), not as an architectural change.

---

# Test Coverage Goals

| Component | Target Coverage |
|-----------|-----------------|
| Routing Engine | ≥ 90% |
| Conversation State | ≥ 95% |
| Router Interface | ≥ 90% |
| Validator | ≥ 95% |
| Dispatcher | ≥ 90% |
| Provider Adapters | ≥ 85% |
| Response Gateway | ≥ 95% |

Overall Target Coverage

```
≥ 90%
```

---

# Acceptance Test Checklist

The POC is considered tested successfully if:

- API endpoints work correctly.
- Conversation state behaves correctly.
- Router produces valid, schema-conformant decisions.
- Router routing-quality evaluation meets its acceptance threshold.
- Fallback Router activates when required.
- Deterministic Default Router guarantees a final decision when both AI routers fail.
- Deterministic retry mechanism functions correctly and always terminates.
- Retry limit and provider-pool-exhaustion guard are enforced.
- Provider abstraction works.
- Dispatcher resolves providers correctly.
- Response Gateway removes known provider identity strings (best-effort).
- Invalid Router decisions never reach providers.
- Provider failures are handled gracefully.
- All automated tests pass.
- Overall code coverage exceeds 90%.

---

# Testing Tools

| Tool | Purpose |
|------|---------|
| pytest | Unit & Integration Testing |
| httpx | API Testing |
| FastAPI TestClient | Endpoint Testing |
| unittest.mock | Mocking Dependencies |
| pytest-cov | Coverage Reports |

---

# Deliverables

The testing phase is complete when the following artifacts are available:

- Unit Test Suite
- Component Test Suite
- Integration Test Suite
- Coverage Report
- Test Execution Report
- POC Demonstration Results

---

# Summary

The testing strategy ensures that every layer of the Routing Engine—from the API Layer to the Response Gateway—is validated independently and as part of the complete request lifecycle. The combination of unit, component, integration, and failure testing provides confidence that the Proof of Concept is reliable, extensible, and ready for demonstration.
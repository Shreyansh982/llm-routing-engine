# Implementation Plan

## Provider-Agnostic LLM Routing Engine

**Document Version:** 1.0

---

# Purpose

This document defines the implementation roadmap for the Proof of Concept (POC).

The goal is to build the routing engine incrementally while ensuring that each milestone is independently testable.

This document intentionally focuses on implementation rather than architecture.

---

# Development Philosophy

The POC follows an incremental development approach.

Each milestone should satisfy the following conditions:

- Independently testable
- Fully functional
- Loosely coupled
- Production-oriented
- Easily extensible

A milestone should never introduce partially implemented components.

---

# Overall Implementation Roadmap

```
Project Setup
      │
      ▼
Core Infrastructure
      │
      ▼
Conversation State
      │
      ▼
Model Registry
      │
      ▼
Router Layer
      │
      ▼
Validation Layer
      │
      ▼
Provider Layer
      │
      ▼
Response Gateway
      │
      ▼
API Layer
      │
      ▼
Testing
```

---

# Milestone 1 — Project Initialization

## Objective

Create the project foundation.

---

## Deliverables

- Folder structure
- Python project
- Virtual environment
- Dependency management
- Configuration management
- Logging setup

---

## Tasks

- Initialize project
- Configure uv
- Configure FastAPI
- Configure Pydantic
- Configure pytest
- Configure .env support
- Create README

---

## Success Criteria

- Project runs successfully
- Configuration loads correctly
- Development environment is functional

---

# Milestone 2 — Core Infrastructure

## Objective

Implement shared project infrastructure.

---

## Deliverables

- Shared schemas
- Base interfaces
- Configuration module
- Dependency injection setup

---

## Tasks

Implement

- RouterRequest (includes `available_providers` with abstract capability descriptors)
- RouterDecision
- ProviderRequest
- ProviderResponse
- ProviderCapability (strengths, speed_tier, context_size)

Create

- Base Router interface
- Base Provider interface

---

## Success Criteria

Core abstractions are complete.

---

# Milestone 3 — Conversation State Manager

## Objective

Implement runtime conversation management.

---

## Responsibilities

- Create conversations
- Retrieve conversations
- Update conversations
- Maintain retry count
- Maintain excluded providers

---

## Data Model

```
conversation_id

original_query

previous_response

attempt_count

max_attempts

excluded_providers

created_at

updated_at
```

---

## Success Criteria

Conversation state behaves correctly across multiple requests.

---

# Milestone 4 — Model Registry

## Objective

Implement provider metadata management.

---

## Responsibilities

- Register providers
- Enable providers
- Disable providers
- Lookup providers

---

## Example Registry

```
provider_a

provider_b

provider_c
```

---

## Success Criteria

Routing Engine can retrieve provider information through the registry.

---

# Milestone 5 — Router Layer

## Objective

Implement routing intelligence.

---

## Components

Primary Router (active by default)

Fallback Router (standby)

Deterministic Default Router (non-AI terminal tier)

---

## Responsibilities

Generate

```
RouterDecision
```

Return

```
ANSWER

RETRY

CLARIFY

STOP
```

The Primary and Fallback Routers must use **schema-constrained / structured output** to
produce the decision (JSON schema, grammar, or the runtime's structured-output mode), with a
single bounded re-parse/repair attempt before the decision is treated as a failure.

The Router receives providers **with abstract capability descriptors** (from the Registry)
so it can route on capability, not opaque IDs.

The Deterministic Default Router contains no reasoning: it selects the first enabled,
non-excluded provider in Registry order, guaranteeing the ladder always terminates.

---

## Success Criteria

Router consistently returns valid, schema-conformant decisions, and the Deterministic
Default Router produces a valid decision whenever both AI routers fail.

---

# Milestone 6 — Decision Validator

## Objective

Prevent invalid routing decisions.

---

## Validation Rules

- Valid JSON
- Valid schema
- Valid provider
- Provider enabled
- Provider not excluded
- Confidence range

---

## Success Criteria

Invalid router outputs never reach execution.

---

# Milestone 7 — Provider Layer

## Objective

Implement provider abstraction.

---

## Components

Provider Dispatcher

Provider Adapter

---

## Responsibilities

Dispatcher

- Resolve provider metadata via the Registry, then instantiate the adapter
  (Registry owns metadata; Dispatcher owns adapter instantiation)

Adapter

- Generate response (receives the prompt only — no routing internals)
- Inject the identity-hiding system prompt (Layer 1 identity defense)
- Normalize response
- Handle provider errors

---

## Success Criteria

Providers are interchangeable without Routing Engine changes.

---

# Milestone 8 — Response Gateway

## Objective

Implement platform response policy.

---

## Responsibilities

Best-effort removal of

- Provider names (known strings/branding)
- Internal metadata
- Routing information

Normalize output.

This is Layer 2 (secondary) of the identity defense; Layer 1 is the adapter system prompt
implemented in Milestone 7.

---

## Example

Input

```
As Claude, I recommend...
```

Output

```
I recommend...
```

---

## Success Criteria

On a best-effort basis, users cannot readily determine which provider generated the
response (system prompt + Gateway filtering together).

---

# Milestone 9 — API Layer

## Objective

Expose the Routing Engine.

---

## Endpoints

```
POST /chat

GET /conversation/{id}

GET /providers

GET /health

GET /router/health

GET /providers/health
```

---

## Success Criteria

Entire Routing Engine accessible through REST APIs.

---

# Milestone 10 — Testing

## Objective

Validate the POC.

---

## Unit Tests

Conversation State

Model Registry

Router

Validator

Dispatcher

Gateway

Provider Adapter

---

## Integration Tests

Complete routing flow

Retry flow

Fallback Router

Provider switching

Conversation persistence

Gateway sanitization

---

## Failure Tests

Router timeout

Malformed JSON

Disabled provider

Unknown provider

Validation failure

Maximum retry reached

---

## Success Criteria

All tests pass successfully.

---

# Project Folder Structure

This mirrors the structure in the project README (see README → Repository Structure for the
per-package descriptions). `core/` holds shared base interfaces only; `routing/` holds the
concrete Routing Engine.

```
router_engine/

├── api/          # FastAPI entry point
├── config/       # configuration loading + provider config
├── core/         # shared base interfaces (no business logic)
├── schemas/      # Pydantic models
├── routing/      # Routing Engine (orchestrator)
├── routers/      # Primary / Fallback / Deterministic Default routers
├── validation/   # Decision Validator
├── registry/     # Model Registry (metadata + capabilities)
├── providers/    # Provider Dispatcher + Adapters
├── gateway/      # Response Gateway
├── state/        # Conversation State Manager
├── tests/        # all test suites incl. routing eval
│
└── README.md
```

---

# Development Order

The recommended implementation sequence is

```
Configuration

↓

Schemas

↓

Conversation State

↓

Model Registry

↓

Router Interface

↓

Primary Router

↓

Fallback Router

↓

Deterministic Default Router

↓

Decision Validator

↓

Provider Dispatcher

↓

Provider Adapters

↓

Response Gateway

↓

Routing Engine

↓

FastAPI

↓

Testing
```

Each component should compile and pass its tests before moving to the next stage.

---

# Risks

| Risk | Mitigation |
|------|------------|
| Invalid / malformed router output | Structured output + Decision Validator |
| Uninformed routing (opaque IDs) | Abstract capability descriptors in Router Request |
| Router unavailable | Fallback Router |
| Both AI routers fail | Deterministic Default Router |
| Infinite retries | Retry limit + provider-pool-exhaustion guard |
| Provider failure | Provider abstraction |
| Provider identity leakage | Adapter system prompt (primary) + Response Gateway (best-effort) |
| Unmeasured routing quality | Routing-quality evaluation set (Testing Strategy) |
| Tight coupling | Interface-based architecture |

---

# Deliverables

At the completion of the POC, the following deliverables should be available:

- Working Routing Engine
- FastAPI application
- Local Router integration
- Fallback Router
- Provider abstraction layer
- Conversation State Manager
- Response Gateway
- Unit test suite
- Integration test suite
- Project documentation
- Architecture documentation

---

# Exit Criteria

The Proof of Concept is considered complete when:

- All milestones are implemented.
- The Routing Engine successfully routes requests using provider capability descriptors.
- Fallback Router functions correctly, and the Deterministic Default Router guarantees a
  final decision if both AI routers fail.
- Deterministic retry mechanism operates as expected and always terminates.
- Provider abstraction allows provider replacement without code changes.
- Response Gateway provides best-effort prevention of provider identity leakage (backed by
  the adapter system prompt).
- The routing-quality evaluation set meets its acceptance threshold.
- All automated tests pass.
- The application can be demonstrated end-to-end using the local Router and configured providers.

---

# Future Enhancements

The following features are intentionally deferred beyond the POC:

- Cost-aware routing
- Latency-aware routing
- Dynamic provider scoring
- Feedback-driven optimization
- Embedding-based routing
- Distributed conversation state
- Authentication and authorization
- Enterprise policy enforcement
- Observability dashboards
- Multi-region deployment

These enhancements can be incorporated without changing the core architecture established by this implementation plan.
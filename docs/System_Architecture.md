# System Architecture

## Provider-Agnostic LLM Routing Engine

**Document Version:** 1.0

---

# Purpose

This document describes the complete architecture of the Provider-Agnostic LLM Routing Engine.

The architecture focuses on:

- Separation of responsibilities
- Provider independence
- Extensibility
- Maintainability
- Production-oriented modularity

The Routing Engine is designed as middleware that sits between client applications and multiple Large Language Model (LLM) providers.

---

# Architectural Principles

The architecture follows these principles.

## 1. Single Intelligent Component

Only one component performs intelligent decision-making: the **Router LLM**.

The Router role has two AI implementations — a Primary Router and a Fallback Router — but
**only one Router instance is ever active for a given decision**. The Fallback Router is a
standby that is engaged only when the Primary Router fails; the two never run
simultaneously. At any moment, at most one AI decision-maker is engaged.

The final tier of the routing ladder — the Deterministic Default Router — is **not** an AI
component; it is a deterministic rule (see Fallback & Failure Ladder).

Every other component is deterministic.

---

## 2. Provider Agnostic

The Routing Engine must never directly depend on

- GPT
- Claude
- Gemini
- DeepSeek
- Qwen

Instead it communicates using Provider IDs.

Example

```
provider_a

provider_b

provider_c
```

The mapping between IDs and actual providers exists only inside the Model Registry.

---

## 3. Loose Coupling

Every major component communicates through interfaces.

Replacing one implementation must not require modifications to existing modules.

---

## 4. Configuration Driven

Providers

Router Models

Retry Limits

Timeouts

Provider Mapping

must all be configurable.

---

## 5. Deterministic Infrastructure

Business logic remains deterministic.

AI reasoning is isolated inside the Router.

---

# High-Level Architecture

```
                                   Client
                                      │
                                      │
                                      ▼
                              FastAPI API Layer
                                      │
                                      ▼
                              Routing Engine
                                      │
        ┌─────────────────────────────┼──────────────────────────────┐
        │                             │                              │
        ▼                             ▼                              ▼
Conversation State             Model Registry               Router Interface
        │                                                     │
        │                                                     │
        │                                  ┌──────────────────┴──────────────────┐
        │                                  ▼                                     ▼
        │                         Primary Router                        Fallback Router
        │                                  │                                     │
        └──────────────────────────────────┴─────────────────────────────────────┘
                                           │
                                           ▼
                                  Decision Validator
                                           │
                                           ▼
                                  Provider Dispatcher
                                           │
          ┌────────────────────────────────┼────────────────────────────────┐
          ▼                                ▼                                ▼
   Provider Adapter A              Provider Adapter B               Provider Adapter C
          │                                │                                │
          └────────────────────────────────┼────────────────────────────────┘
                                           ▼
                                  Response Gateway
                                           │
                                           ▼
                                         Client
```

---

# Architectural Layers

The architecture is divided into four logical layers.

---

## Layer 1

### Presentation Layer

Components

- Client
- FastAPI

Responsibilities

- Receive requests
- Return responses

No routing logic exists here.

---

## Layer 2

### Routing Layer

Components

- Routing Engine
- Router Interface
- Primary Router
- Fallback Router

Responsible for deciding

"What should happen next?"

---

## Layer 3

### Execution Layer

Components

- Decision Validator
- Provider Dispatcher
- Provider Adapters

Responsible for executing routing decisions.

No intelligent decisions occur here.

---

## Layer 4

### Infrastructure Layer

Components

- Conversation State
- Model Registry
- Configuration

Responsible for runtime information only.

---

# Component Overview

| Component | Type | Intelligent |
|------------|------|------------|
| API Layer | Deterministic | No |
| Routing Engine | Deterministic | No |
| Conversation State | Deterministic | No |
| Model Registry | Deterministic | No |
| Primary Router | AI | Yes (active by default) |
| Fallback Router | AI | Yes (standby; active only on Primary failure) |
| Deterministic Default Router | Deterministic | No |
| Decision Validator | Deterministic | No |
| Provider Dispatcher | Deterministic | No |
| Provider Adapter | Deterministic | No |
| Response Gateway | Deterministic | No |

The Primary and Fallback Routers are two implementations of the **same single AI role** and
are mutually exclusive at runtime — never both active for one decision. Everything else is
deterministic.

---

# Component Responsibilities

---

## API Layer

Responsibilities

- Receive requests
- Validate request format
- Forward requests
- Return responses

Never performs routing.

---

## Routing Engine

The Routing Engine is the heart of the application.

Responsibilities

- Build Router Request (injecting provider capabilities from the Registry)
- Load Conversation State
- Update Conversation State
- Enforce deterministic retry guards (max_attempts, provider-pool exhaustion)
- Invoke Router
- Invoke Fallback Router
- Apply Deterministic Default Router when both AI Routers fail
- Validate Router Decision
- Dispatch Provider
- Receive Provider Response
- Pass Response through Gateway

The Routing Engine NEVER performs routing intelligence. Enforcing retry guards and applying
the deterministic default are mechanical rules, not reasoning.

---

## Conversation State Manager

Stores runtime state.

State contains

```
conversation_id

original_query

previous_response

attempt_count

max_attempts

excluded_providers

timestamps
```

**Scope (POC).** Conversation state is deliberately *routing/retry bookkeeping*, not a
full multi-turn memory. It tracks a single `original_query`, the single most recent
`previous_response`, and the retry accounting needed to re-route (`attempt_count`,
`max_attempts`, `excluded_providers`). Rich multi-turn history is intentionally out of scope
for the POC and is listed under future persistent conversation storage.

This component never communicates with providers.

---

## Model Registry

Stores metadata about providers, including each provider's **abstract capability
descriptor** (strengths, speed_tier, context_size). These descriptors are vendor-neutral
and are the source the Routing Engine reads when building the Router Request.

Example

```
provider_a

provider_b

provider_c
```

The Registry is the single owner of the ID → real-provider mapping.

Example

```
provider_a

↓

DeepSeek
```

The Router never knows this mapping — it only ever sees provider IDs and their abstract
capabilities.

**Registry vs Dispatcher ownership**

- **Model Registry** owns provider *metadata*: IDs, enabled state, and capability
  descriptors. It answers "what providers exist and what are they good at."
- **Provider Dispatcher** owns *adapter resolution*: turning a validated provider ID into a
  concrete adapter instance. It answers "how do I actually call this provider."

The Registry never instantiates adapters; the Dispatcher never stores metadata.

---

## Router Interface

Defines the common interface implemented by every Router.

Implementations

Primary Router

Fallback Router

The Routing Engine depends only on this interface.

---

## Primary Router

The only intelligent decision-maker (active by default; see Single Intelligent Component).

Receives

- User Query
- Previous Response
- User Follow-up
- Retry State
- Available Providers **with abstract capability descriptors** (strengths, speed_tier,
  context_size)

The capability descriptors are what make routing *informed* rather than a guess between
opaque IDs.

Returns

```
ANSWER

RETRY

CLARIFY

STOP
```

along with

- selected provider
- confidence
- reason

The Router **must produce this decision using schema-constrained / structured output**
(e.g. a JSON schema or grammar-constrained decoding, or the model runtime's structured
output mode). This is a hard requirement: it prevents the malformed-JSON failure mode from
being the common case. A bounded single re-parse/repair attempt is permitted before the
decision is treated as a failure and the Fallback Router is engaged.

**Action semantics**

| Action | Routing Engine behavior |
|--------|-------------------------|
| ANSWER | Dispatch to `selected_provider`, return its (gateway-processed) response |
| CLARIFY | Return the clarifying question to the client; no provider dispatched; `attempt_count` unchanged |
| RETRY | Treat as a request to re-route: exclude the previous provider and re-invoke, subject to the deterministic retry guards (max_attempts, pool exhaustion) |
| STOP | Return a terminal message; no provider dispatched |

---

## Fallback Router

A standby AI Router (same interface as the Primary Router) invoked automatically when the
Primary Router's decision cannot be used:

- timeout
- router unavailable
- malformed JSON
- invalid provider
- invalid action

The Fallback Router is never active at the same time as the Primary Router.

---

## Deterministic Default Router

A **deterministic, non-AI** terminal tier. It contains no reasoning: it simply selects the
first enabled, non-excluded provider in Registry order. It exists so the routing ladder
always terminates even if both AI Routers fail.

---

## Fallback & Failure Ladder

Every routing decision — and every fallback — passes through the Decision Validator. The
ladder is strictly ordered and finite:

```
1. Primary Router (AI)
        │  decision → Decision Validator
        │  invalid / unavailable / timeout / malformed
        ▼
2. Fallback Router (AI)
        │  decision → Decision Validator
        │  invalid / unavailable / timeout / malformed
        ▼
3. Deterministic Default Router (non-AI)
        │  first enabled, non-excluded provider
        │  none available
        ▼
4. Controlled error (503 ROUTER_FAILURE) — no provider dispatched
```

Because tier 3 is deterministic and tier 4 is a hard stop, the ladder cannot loop
indefinitely. The Validator runs after each AI tier, so an invalid Fallback decision
demotes to the Deterministic Default rather than re-entering the AI tiers.

---

## Decision Validator

Ensures Router decisions are valid. The Validator is the single gate that every AI Router
decision passes through — it is what *detects* the failure conditions that drive the
Fallback & Failure Ladder above.

Validation includes

- JSON
- Schema
- Confidence
- Provider Exists
- Provider Enabled
- Provider Not Excluded

Invalid decisions never reach providers. On validation failure the engine escalates to the
next tier of the ladder (Primary → Fallback → Deterministic Default → error); it does not
retry the same tier.

---

## Provider Dispatcher

Maps Provider IDs to Provider Adapters.

Example

```
provider_a

↓

ProviderAdapterA
```

---

## Provider Adapters

Responsible for communication with external providers.

Responsibilities

- Generate response
- Handle provider errors
- Normalize provider output

Adapters contain all provider-specific logic.

---

## Response Gateway

Every provider response passes through this component. It is the **secondary (best-effort)**
layer of a two-layer identity-hiding defense:

- **Primary defense** — each Provider Adapter injects a system prompt telling the model not
  to reveal its identity, vendor, or model name. Most leakage is prevented at the source.
- **Secondary defense (this component)** — deterministic best-effort filtering of known
  identity strings and branding.

Responsibilities

- Best-effort removal of provider identity leakage
- Remove known provider-specific wording
- Remove provider metadata
- Enforce platform response policy
- Return standardized response

The goal is that the client cannot readily tell which provider answered. This is a
best-effort guarantee: because a language model can self-identify in unbounded ways, the
Gateway alone cannot promise perfect masking — which is why the adapter system prompt is
the primary defense.

---

# Retry Architecture

Retry is managed by the Routing Engine and is driven by a **deterministic, explicit
signal** — never by inferred user satisfaction. The engine performs no sentiment analysis.

Normal flow

```
User

↓

Router

↓

Provider

↓

Response Gateway

↓

User
```

A retry occurs when either deterministic trigger fires:

1. The client sends `POST /chat` with `retry: true` for the same `conversation_id`, or
2. The active Router returns the `RETRY` action.

Both funnel through the same handling:

```
excluded_providers += previous provider

↓

attempt_count++

↓

Router invoked again (with the reduced provider set)
```

**Termination guards (evaluated before re-invoking the Router):**

```
if attempt_count >= max_attempts        → return STOP (no Router call)
if all enabled providers are excluded   → return STOP (no Router call)
```

These two guards guarantee the retry loop is finite regardless of Router behavior.

---

# Configuration Architecture

All runtime values must be externally configurable.

Configuration includes

- Router URL
- Router Model
- Fallback Router URL
- Fallback Router Model
- Retry Limit
- Timeout
- Provider Mapping (ID → real provider)
- Provider Capability Descriptors (abstract: strengths, speed_tier, context_size)

No configuration values should be hardcoded.

**Configuration ownership (POC).** Providers, their mappings, and their capability
descriptors are defined in configuration and loaded **once at startup** to populate the
Model Registry. In the POC the Registry is effectively read-only after startup; runtime
mutation of the provider set (e.g. `POST /providers/register`) is out of scope and deferred
to the roadmap. This keeps the system "configuration-driven" without a runtime provider
management surface.

---

# Extensibility

The architecture supports future additions without modifying existing components.

Examples

Adding a new provider

- Create Provider Adapter
- Register Provider
- Update Configuration

Adding a new Router

- Implement Router Interface
- Register Implementation

No Routing Engine modifications required.

---

# Architectural Constraints

The following are intentionally excluded from the POC.

- Embedding Router
- Vector Database
- User Memory
- Analytics
- Cost Optimization
- Latency Optimization
- Enterprise Authentication
- Rate Limiting
- Kubernetes Deployment
- Distributed State Storage
- Reinforcement Learning
- Feedback Learning

These components belong to future iterations.

---

# Summary

The architecture follows four key principles.

1. One intelligent decision-maker

2. Deterministic infrastructure

3. Provider independence

4. Configuration-driven extensibility

This separation ensures the Routing Engine remains modular, maintainable, and easily extensible while supporting future enterprise-scale enhancements without requiring architectural redesign.
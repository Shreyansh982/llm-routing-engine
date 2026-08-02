# Component Specification

## Provider-Agnostic LLM Routing Engine

**Document Version:** 1.0

---

# Purpose

This document specifies every major component of the LLM Routing Engine.

For every component this document defines:

- Purpose
- Responsibilities
- Inputs
- Outputs
- Public Interface
- Dependencies
- Failure Conditions
- Future Extensibility

This document serves as the implementation specification for the POC.

---

# Component Overview

| Component | Intelligent | Responsibility |
|------------|------------|----------------|
| API Layer | ❌ | Entry point |
| Routing Engine | ❌ | Workflow execution |
| Conversation State Manager | ❌ | Runtime state |
| Model Registry | ❌ | Provider metadata |
| Router Interface | ❌ | Router abstraction |
| Primary Router | ✅ | Intelligent routing (active by default) |
| Fallback Router | ✅ | Backup routing (standby; active only on Primary failure) |
| Deterministic Default Router | ❌ | Non-AI terminal routing tier |
| Decision Validator | ❌ | Validate router output |
| Provider Dispatcher | ❌ | Provider selection |
| Provider Adapter | ❌ | Provider communication |
| Response Gateway | ❌ | Response sanitization |

The Primary and Fallback Routers are two implementations of a **single AI role** and are
mutually exclusive at runtime — at most one is active per decision. The Deterministic
Default Router is a non-AI safety net. Thus the "single AI decision-maker" principle holds:
only one intelligent decision is ever in flight.

---

# 1. API Layer

## Purpose

Acts as the public interface to the Routing Engine.

---

## Responsibilities

- Receive user requests
- Validate request payload
- Forward request to Routing Engine
- Return final response

---

## Inputs

HTTP Request

```json
{
    "conversation_id":"...",
    "message":"..."
}
```

---

## Outputs

HTTP Response

```json
{
    "response":"..."
}
```

---

## Dependencies

- Routing Engine

---

## Must Never

- Route requests
- Call providers
- Store conversation state

---

# 2. Routing Engine

## Purpose

Coordinates the complete request lifecycle.

This is the central deterministic component.

---

## Responsibilities

- Build routing context
- Retrieve conversation state
- Invoke Router
- Invoke fallback Router
- Validate Router decision
- Dispatch Provider
- Receive Provider response
- Pass response through Gateway
- Update conversation state

---

## Inputs

```python
RoutingRequest
```

---

## Outputs

```python
RoutingResponse
```

---

## Dependencies

- Conversation State Manager
- Model Registry
- Router Interface
- Decision Validator
- Provider Dispatcher
- Response Gateway

---

## Must Never

- Decide providers
- Rank providers
- Contain AI logic

---

# 3. Conversation State Manager

## Purpose

Stores runtime information for each conversation.

**Scope (POC):** this is *routing/retry bookkeeping*, not full multi-turn memory. It holds a
single `original_query`, the most recent `previous_response`, and the retry accounting
fields. Rich multi-turn history is out of scope for the POC (see roadmap: persistent
conversation storage).

---

## Responsibilities

Create conversation

Load conversation

Update conversation

Increment retry count

Maintain excluded providers

Persist previous response

---

## State Model

```python
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

## Public Interface

```python
create()

load()

update()

increment_attempt()

exclude_provider()

reset()
```

---

## Dependencies

None

---

# 4. Model Registry

## Purpose

Maintains provider metadata.

---

## Responsibilities

Store

- Provider IDs
- Enabled status
- Configuration
- Abstract capability descriptors (strengths, speed_tier, context_size)
- ID → real-provider mapping (single owner)

The Registry owns provider **metadata only**. It does **not** instantiate adapters — that is
the Provider Dispatcher's job. This keeps a clean split: Registry answers "what exists and
what is it good at"; Dispatcher answers "how do I call it."

Capability descriptors are read by the Routing Engine and injected into the Router Request
so the Router can route on capability rather than on opaque IDs.

---

## Example

```python
provider_a  # {strengths:[reasoning, coding], speed_tier:standard, context_size:large}

provider_b  # {strengths:[creative_writing, summarization], speed_tier:fast, context_size:standard}

provider_c
```

---

## Public Interface

```python
get_provider()          # includes capability descriptors

list_providers()

is_enabled()

register()               # startup-time population from configuration

disable()                # startup-time / config-driven
```

`register()` and `disable()` are used to **populate the Registry from configuration at
startup**. In the POC the Registry is effectively read-only afterward; there is no runtime
provider-management API (that is deferred — see `POST /providers/register` under Future
Endpoints).

---

## Dependencies

Configuration

---

# 5. Router Interface

## Purpose

Defines the contract implemented by every Router.

---

## Interface

```python
decide(
    RouterRequest
)
```

Returns

```python
RouterDecision
```

---

## Implementations

PrimaryRouter

FallbackRouter

---

## Benefits

Allows replacing Router models without changing Routing Engine.

---

# 6. Primary Router

## Purpose

Perform intelligent routing.

---

## Responsibilities

Given

- Query
- Previous response
- Retry state
- Available providers **with abstract capability descriptors** (strengths, speed_tier,
  context_size)

Return

- Action
- Provider
- Confidence
- Reason

The capability descriptors are what allow an *informed* choice; without them the Router
would be guessing between opaque IDs.

---

## Structured Output Requirement

The Router **must** emit its decision using schema-constrained / structured output — a JSON
schema, grammar-constrained decoding, or the model runtime's structured/JSON output mode.
Emitting free-form prose that happens to contain JSON is not acceptable. A single bounded
re-parse/repair attempt is permitted; if it still fails, the decision is treated as a
failure and the next tier of the ladder is engaged.

---

## Action Semantics

| Action | Engine behavior |
|--------|-----------------|
| ANSWER | Dispatch to `selected_provider` |
| CLARIFY | Return clarifying question to client; no dispatch; `attempt_count` unchanged |
| RETRY | Re-route with previous provider excluded, subject to retry guards |
| STOP | Return terminal message; no dispatch |

---

## Public Interface

```python
decide()
```

---

## Output

```json
{
    "action":"ANSWER",
    "selected_provider":"provider_a",
    "confidence":0.93,
    "reason":"..."
}
```

---

## Dependencies

Local Router Model (invoked in structured-output mode)

---

# 7. Fallback Router

## Purpose

Provide routing decisions when Primary Router fails.

---

## Trigger Conditions

Timeout

Malformed JSON

Router unavailable

Invalid schema

Invalid provider

Invalid action

These conditions are *detected by the Decision Validator* (or by transport-level
timeout/unavailability) after the Primary Router runs.

---

## Runtime Exclusivity

The Fallback Router is a **standby**. It runs only when the Primary Router's decision cannot
be used, and never concurrently with the Primary Router. This preserves the single active
AI decision-maker principle.

---

## Interface

Same as Primary Router (including the structured-output requirement).

---

# 7a. Deterministic Default Router

## Purpose

A **non-AI** terminal routing tier that guarantees the routing ladder always terminates,
even if both the Primary and Fallback Routers fail.

---

## Behavior

Deterministic rule only — no reasoning:

- Select the first **enabled, non-excluded** provider in Registry order.
- If no such provider exists, return no decision and the engine raises a controlled
  `ROUTER_FAILURE` (HTTP 503).

---

## Complete Failure Ladder

```
Primary Router (AI)
   → Fallback Router (AI)
      → Deterministic Default Router (non-AI)
         → Controlled error (503)
```

Each AI tier's output is validated; on failure the engine escalates to the next tier and
never re-enters a prior tier, so the ladder is strictly finite.

---

## Public Interface

```python
select_default()
```

---

# 8. Decision Validator

## Purpose

Prevent invalid Router decisions from reaching execution.

---

## Validation Rules

Valid JSON

Valid Schema

Valid Action

Valid Provider

Provider Enabled

Provider Not Excluded

Confidence

0 ≤ confidence ≤ 1

---

## Public Interface

```python
validate()
```

---

## Failure

Signals an invalid decision (e.g. `ValidationError`). The Routing Engine reacts by escalating
to the next tier of the Failure Ladder — Primary → Fallback → Deterministic Default → 503 —
rather than surfacing the error immediately. A validation failure is therefore the mechanism
that *triggers* the fallback, not a dead end.

---

# 9. Provider Dispatcher

## Purpose

Convert a validated Provider ID into a concrete Provider Adapter and invoke it.

**Boundary with the Registry:** the Dispatcher asks the Registry *whether* a provider exists
and is enabled (metadata), then owns the step the Registry does not — resolving and
instantiating the adapter. The Registry never instantiates adapters.

---

## Responsibilities

Look up provider metadata (via Registry)

Resolve and instantiate the adapter

Forward request (prompt only — no routing internals)

Return response

---

## Public Interface

```python
dispatch()
```

---

## Dependencies

Model Registry

Provider Adapters

---

# 10. Provider Adapter

## Purpose

Encapsulate provider-specific implementation.

---

## Responsibilities

Generate responses

Inject the identity-hiding system prompt (Layer 1 of the two-layer identity defense —
instructs the model not to reveal its identity/vendor/model)

Normalize outputs

Handle provider exceptions

Health checks

---

## Interface

```python
generate(prompt)   # receives only the prompt — never excluded_providers / attempt_count

health()

metadata()
```

---

## Future Providers

OpenAI

Anthropic

Google

Azure

Groq

Together

Fireworks

No Routing Engine changes required.

---

# 11. Response Gateway

## Purpose

Produce platform-compliant responses on a **best-effort** basis.

Every response generated by a provider MUST pass through this component. It is **Layer 2**
(secondary) of the two-layer identity defense; **Layer 1** is the identity-hiding system
prompt injected by each Provider Adapter, which prevents most leakage at the source.

Because a language model can self-identify in unbounded ways, the Gateway does not promise
perfect masking — it applies deterministic best-effort filtering of known identity strings.

---

## Responsibilities

Best-effort removal of provider identity (known strings/branding)

Remove provider metadata

Enforce platform response policy

Normalize formatting

Return final response

---

## Example

Input

```
As Claude, I think...
```

Output

```
I think...
```

---

## Public Interface

```python
process()
```

---

## Dependencies

None

---

# Component Dependencies

```
API Layer
      │
      ▼
Routing Engine
      │
 ┌────┼──────────────────────────────┐
 ▼    ▼                              ▼
State Registry                  Router Interface
 │                                 │
 ▼                                 ▼
Conversation                Primary/Fallback
 │                                 │
 └──────────────┬──────────────────┘
                ▼
       Decision Validator
                │
                ▼
      Provider Dispatcher
                │
                ▼
       Provider Adapter
                │
                ▼
      Response Gateway
```

---

# Component Communication Rules

API Layer

↓

Routing Engine

Only

---

Routing Engine

↓

Router

Only through Router Interface

---

Routing Engine

↓

Providers

Only through Provider Dispatcher

---

Providers

↓

Client

Never directly

Always through Response Gateway

---

# Failure Responsibilities

| Component | Handles Failure |
|------------|----------------|
| API Layer | Invalid requests |
| Routing Engine | Retry lifecycle |
| Router | Routing decision |
| Fallback Router | Primary failure |
| Deterministic Default Router | Both AI routers failed |
| Validator | Invalid decision |
| Dispatcher | Provider lookup |
| Adapter | Provider errors |
| Gateway | Response sanitization |

---

# Design Principles

Every component follows:

- Single Responsibility Principle
- Dependency Inversion Principle
- Open/Closed Principle
- Interface Segregation Principle

No component should require modification when adding:

- a new Router
- a new Provider
- a new configuration

Only new implementations should be added.

---

# Summary

The architecture separates intelligence from execution.

Only the Router performs reasoning.

Every remaining component performs deterministic operations through clearly defined interfaces, making the system modular, maintainable, testable, and extensible.
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

Only one component performs intelligent decision-making.

**Router LLM**

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
| Primary Router | AI | Yes |
| Fallback Router | AI | Yes |
| Decision Validator | Deterministic | No |
| Provider Dispatcher | Deterministic | No |
| Provider Adapter | Deterministic | No |
| Response Gateway | Deterministic | No |

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

- Build Router Request
- Load Conversation State
- Update Conversation State
- Invoke Router
- Invoke Fallback Router
- Validate Router Decision
- Dispatch Provider
- Receive Provider Response
- Pass Response through Gateway

The Routing Engine NEVER performs routing intelligence.

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

This component never communicates with providers.

---

## Model Registry

Stores metadata about providers.

Example

```
provider_a

provider_b

provider_c
```

Only registry knows actual provider mappings.

Example

```
provider_a

↓

DeepSeek
```

The Router never knows this mapping.

---

## Router Interface

Defines the common interface implemented by every Router.

Implementations

Primary Router

Fallback Router

The Routing Engine depends only on this interface.

---

## Primary Router

The only intelligent decision-maker.

Receives

- User Query
- Previous Response
- User Follow-up
- Retry State
- Available Providers

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

---

## Fallback Router

Invoked automatically when

- timeout
- router unavailable
- malformed JSON
- invalid provider
- invalid action

Uses the same interface as the Primary Router.

---

## Decision Validator

Ensures Router decisions are valid.

Validation includes

- JSON
- Schema
- Confidence
- Provider Exists
- Provider Enabled
- Provider Not Excluded

Invalid decisions never reach providers.

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

Every provider response passes through this component.

Responsibilities

- Remove provider identity leakage
- Remove provider-specific wording
- Remove provider metadata
- Enforce platform response policy
- Return standardized response

The client must never know which provider answered.

---

# Retry Architecture

Retry is managed by the Routing Engine.

Flow

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

If the user indicates dissatisfaction

```
attempt_count++

↓

excluded_providers += previous provider

↓

Router invoked again
```

If

```
attempt_count >= max_attempts
```

Routing Engine returns

```
STOP
```

without invoking the Router.

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
- Provider Mapping

No configuration values should be hardcoded.

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
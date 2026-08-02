# Provider-Agnostic LLM Routing Engine

<div align="center">

![Python](https://img.shields.io/badge/Python-3.13-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-Latest-green)
![Pydantic](https://img.shields.io/badge/Pydantic-v2-red)
![Status](https://img.shields.io/badge/Status-POC-orange)
![License](https://img.shields.io/badge/License-MIT-lightgrey)

**An intelligent, provider-agnostic routing middleware for Large Language Models**

</div>

---

# Overview

The **Provider-Agnostic LLM Routing Engine** is a production-oriented Proof of Concept (POC) that intelligently routes user requests to the most appropriate Large Language Model (LLM) provider while remaining completely provider-independent.

Instead of exposing provider selection to end users or hardcoding routing rules, the Routing Engine introduces a dedicated **Router LLM** responsible for selecting the best provider based on the current conversation context.

The system is designed around modular software engineering principles, making it easy to extend with additional providers and routing strategies without requiring architectural changes.

---

# Key Features

- 🤖 Intelligent routing using a local Router LLM
- 🔄 Automatic retry with alternate providers
- 🛡️ Fallback Router for increased reliability
- 🔌 Provider-agnostic architecture
- 🧩 Modular component design
- 🔒 Provider identity masking through Response Gateway
- 📋 Conversation state management
- ⚙️ Configuration-driven provider registry
- 🧪 Comprehensive testing strategy
- 📦 Clean Architecture with SOLID principles

---

# System Architecture

```
                                   Client
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

# Repository Structure

```
router_engine/

├── api/
├── config/
├── core/
├── gateway/
├── providers/
├── registry/
├── routers/
├── routing/
├── schemas/
├── state/
├── tests/
├── validation/
│
├── .env.example
├── pyproject.toml
├── README.md
└── docs/
```

---

# Technology Stack

| Category | Technology |
|----------|------------|
| Language | Python 3.13 |
| API Framework | FastAPI |
| Validation | Pydantic v2 |
| HTTP Client | httpx |
| Package Manager | uv |
| Testing | pytest |
| Router Models | Local LLM (Configurable) |

---

# Project Components

| Component | Responsibility |
|-----------|----------------|
| API Layer | Entry point |
| Routing Engine | Request lifecycle management |
| Conversation State Manager | Runtime conversation state |
| Model Registry | Provider metadata |
| Router | Intelligent routing decisions |
| Fallback Router | Backup routing |
| Decision Validator | Validate routing decisions |
| Provider Dispatcher | Resolve provider adapters |
| Provider Adapter | Provider communication |
| Response Gateway | Provider identity masking |

---

# Request Lifecycle

```
User

↓

FastAPI

↓

Routing Engine

↓

Router

↓

Decision Validator

↓

Provider Dispatcher

↓

Selected Provider

↓

Response Gateway

↓

User
```

If the user is dissatisfied:

```
attempt++

↓

exclude previous provider

↓

Router invoked again

↓

Alternate Provider

↓

Response Gateway

↓

User
```

The retry process continues until:

- A satisfactory response is produced, or
- The maximum retry limit is reached.

---

# Design Principles

The Routing Engine is built upon the following principles:

### Single Intelligent Component

Only the Router LLM performs intelligent decision making.

All remaining components are deterministic.

---

### Provider Agnostic

The Routing Engine never depends on vendor-specific implementations.

Providers are replaceable through configuration only.

---

### Loose Coupling

Every component communicates through interfaces.

Replacing one implementation does not require modifying existing modules.

---

### Configuration Driven

Providers

Router Models

Retry Limits

Timeouts

Mappings

are externally configurable.

---

### Extensibility

New providers can be introduced without modifying the Routing Engine.

---

# Current Scope (POC)

The current implementation demonstrates:

- Intelligent routing
- Provider abstraction
- Retry handling
- Fallback router
- Response Gateway
- Conversation state management
- Configuration-driven provider mapping

---

# Out of Scope

The following features are intentionally excluded from the Proof of Concept:

- Embedding-based routing
- Vector databases
- Long-term memory
- User profiling
- Cost-aware routing
- Analytics
- Authentication
- Enterprise policy engine
- Kubernetes deployment
- Multi-region deployment

These capabilities are documented in the project roadmap.

---

# Running the Project

## Clone

```bash
git clone <repository-url>
cd router_engine
```

---

## Install

```bash
uv sync
```

---

## Configure

Create a `.env` file.

Example:

```env
PRIMARY_ROUTER_URL=
PRIMARY_ROUTER_MODEL=

FALLBACK_ROUTER_URL=
FALLBACK_ROUTER_MODEL=

MAX_RETRIES=3

REQUEST_TIMEOUT=30
```

---

## Start

```bash
uv run uvicorn api.main:app --reload
```

---

## Run Tests

```bash
pytest
```

---

# Documentation

Project documentation is available in the `docs/` directory.

| Document | Description |
|----------|-------------|
| 01_Project_Overview.md | Project vision and scope |
| 02_System_Architecture.md | Complete architecture |
| 03_Component_Specification.md | Component specifications |
| 04_API_Contracts.md | API definitions |
| 05_Implementation_Plan.md | Development roadmap |
| 06_Testing_Strategy.md | Testing approach |
| 07_Future_Roadmap.md | Future enhancements |

---

# Future Enhancements

Planned improvements include:

- Cost-aware routing
- Latency-aware routing
- Provider health scoring
- Embedding-assisted routing
- Enterprise policy engine
- Persistent conversation storage
- Monitoring and observability
- Multi-region deployment

---

# License

This project is released under the MIT License.

---

# Author

**Shreyansh Rathore**

B.Tech CSE (AI & ML)

Proof of Concept developed as part of an internship project exploring intelligent routing architectures for Large Language Models.
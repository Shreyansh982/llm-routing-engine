from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.settings import Settings
from schemas.models import InputModality, TaskGroup


EXPECTED = {
    "g1_google_flash_lite": ("general", "google", "gemini-3.5-flash-lite", 1),
    "g2_openrouter_qwen": ("general", "openrouter", "qwen/qwen3.8-27b", 2),
    "g3_groq_oss20b": ("general", "groq", "openai/gpt-oss-20b", 3),
    "g4_nvidia_deepseek": ("general", "nvidia", "deepseek-ai/deepseek-v4.1-flash", 4),
    "c1_groq_oss120b": ("coding_reasoning", "groq", "openai/gpt-oss-120b", 1),
    "c2_nvidia_kimi": ("coding_reasoning", "nvidia", "moonshotai/kimi-k3", 2),
    "c3_openrouter_north": ("coding_reasoning", "openrouter", "cohere/north-mini-code:free", 3),
    "c4_openrouter_laguna": ("coding_reasoning", "openrouter", "poolside/laguna-s-2.1:free", 4),
    "w1_openrouter_gemma31b": ("creative", "openrouter", "google/gemma-4-31b-it:free", 1),
    "w2_google_flash": ("creative", "google", "gemini-3.6-flash", 2),
    "m1_nvidia_nemotron_omni": (
        "multimodal",
        "nvidia",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        1,
    ),
    "m2_openrouter_gemma26b": (
        "multimodal",
        "openrouter",
        "google/gemma-4-26b-a4b-it:free",
        2,
    ),
}


def env_example() -> dict[str, str]:
    values = {}
    for line in Path(".env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.lower()] = value
    return values


def settings_for(providers: list[dict[str, object]]) -> Settings:
    return Settings(
        _env_file=None,
        google_endpoint="http://google.invalid/chat/completions",
        nvidia_endpoint="http://nvidia.invalid/chat/completions",
        openrouter_endpoint="http://openrouter.invalid/chat/completions",
        groq_endpoint="http://groq.invalid/chat/completions",
        google_api_key="key",
        nvidia_api_key="key",
        OPENROUTER_API_KEY="key",
        groq_api_key="key",
        primary_router_backend="google",
        primary_router_model="gemini-3.8-flash",
        fallback_router_backend="nvidia",
        fallback_router_model="nvidia/nemotron-3.5-lightning-30b-a3b",
        request_timeout=20,
        router_timeout=10,
        providers_json=json.dumps(providers),
    )


def test_env_example_contains_exact_router_and_task_model_topology() -> None:
    values = env_example()
    for key in ("google_api_key", "nvidia_api_key", "openrouter_api_key", "groq_api_key"):
        values.pop(key)
    values.update(
        {
            "google_api_key": "key",
            "nvidia_api_key": "key",
            "OPENROUTER_API_KEY": "key",
            "groq_api_key": "key",
        }
    )
    settings = Settings(_env_file=None, **values)
    providers = settings.provider_configs()

    assert settings.primary_router_config().backend == "google"
    assert settings.primary_router_config().model == "gemini-3.8-flash"
    assert settings.fallback_router_config().backend == "nvidia"
    assert settings.fallback_router_config().model == "nvidia/nemotron-3.5-lightning-30b-a3b"
    assert settings.primary_router_config().timeout == 10
    assert settings.fallback_router_config().timeout == 10
    assert all(provider.timeout == 20 for provider in providers)
    assert settings.overall_request_timeout == 60
    assert settings.request_timeout < settings.overall_request_timeout
    assert len(providers) == 12
    assert len({provider.id for provider in providers}) == 12
    assert {
        provider.id: (
            provider.task_group.value,
            provider.backend,
            provider.model,
            provider.priority,
        )
        for provider in providers
    } == EXPECTED
    assert {provider.task_group for provider in providers} == set(TaskGroup)
    assert all(
        InputModality.IMAGE in provider.capabilities.input_modalities
        for provider in providers
        if provider.task_group == TaskGroup.MULTIMODAL
    )


def minimal_provider(provider_id: str, group: str, priority: int, backend: str = "google") -> dict[str, object]:
    return {
        "id": provider_id,
        "backend": backend,
        "model": provider_id,
        "task_group": group,
        "priority": priority,
        "capabilities": {
            "strengths": [group],
            "speed_tier": "test",
            "context_size": "test",
            "input_modalities": ["text", "image"] if group == "multimodal" else ["text"],
        },
    }


def valid_minimal_topology() -> list[dict[str, object]]:
    return [
        minimal_provider("general_one", "general", 1),
        minimal_provider("coding_one", "coding_reasoning", 1),
        minimal_provider("creative_one", "creative", 1),
        minimal_provider("multimodal_one", "multimodal", 1),
    ]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda items: items + [dict(items[0])],
        lambda items: items + [minimal_provider("general_two", "general", 1)],
        lambda items: items[:-1],
        lambda items: [*items[:-1], {**items[-1], "capabilities": {**items[-1]["capabilities"], "input_modalities": ["text"]}}],
        lambda items: [{**items[0], "backend": "unknown"}, *items[1:]],
    ],
)
def test_invalid_provider_topologies_fail_cleanly(mutate) -> None:
    with pytest.raises(ValueError):
        settings_for(mutate(valid_minimal_topology())).provider_configs()


def test_malformed_provider_json_fails_cleanly() -> None:
    settings = settings_for(valid_minimal_topology()).model_copy(update={"providers_json": "not-json"})
    with pytest.raises(ValueError, match="PROVIDERS_JSON"):
        settings.provider_configs()


def test_router_and_task_provider_timeouts_are_independent() -> None:
    settings = settings_for(valid_minimal_topology()).model_copy(
        update={"request_timeout": 47, "router_timeout": 2}
    )

    assert settings.primary_router_config().timeout == 2
    assert settings.fallback_router_config().timeout == 2
    assert all(provider.timeout == 47 for provider in settings.provider_configs())

    changed = settings.model_copy(update={"request_timeout": 31, "router_timeout": 7})
    assert changed.primary_router_config().timeout == 7
    assert all(provider.timeout == 31 for provider in changed.provider_configs())


def test_default_provider_budget_leaves_time_for_fallback(monkeypatch) -> None:
    from api.main import build_engine

    monkeypatch.delenv("REQUEST_TIMEOUT", raising=False)
    monkeypatch.delenv("OVERALL_REQUEST_TIMEOUT", raising=False)
    settings = Settings(_env_file=None)
    assert settings.request_timeout == 20
    assert settings.overall_request_timeout == 60
    engine = build_engine(settings)
    assert engine._overall_request_timeout == 60
    assert all(config.timeout == 20 for config in engine.registry.list_providers())

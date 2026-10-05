"""Externally supplied runtime settings for the proof of concept."""

from __future__ import annotations

import json
from functools import lru_cache

from pydantic import BaseModel, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic.aliases import AliasChoices

from schemas.models import InputModality, ProviderConfig, TaskGroup


class RouterBackendConfig(BaseModel):
    """Private Router connection configuration; never passed to the Routing Engine."""

    backend: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key: SecretStr
    timeout: float = Field(gt=0)
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_tokens: int = Field(default=96, gt=0)
    reasoning_effort: str | None = "none"


class Settings(BaseSettings):
    """Configuration loaded once at application startup from environment or ``.env``."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openrouter_base_url: str | None = None
    groq_base_url: str | None = None
    google_base_url: str | None = None
    nvidia_base_url: str | None = None
    openrouter_endpoint: str | None = None
    groq_endpoint: str | None = None
    google_endpoint: str | None = None
    nvidia_endpoint: str | None = None
    # OPENROOUTER_API_KEY is retained as a read-only alias for the pre-existing local .env.
    openrouter_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("OPENROUTER_API_KEY", "OPENROOUTER_API_KEY")
    )
    groq_api_key: SecretStr | None = None
    google_api_key: SecretStr | None = None
    nvidia_api_key: SecretStr | None = None
    primary_router_backend: str | None = None
    primary_router_model: str | None = None
    fallback_router_backend: str | None = None
    fallback_router_model: str | None = None
    max_retries: int = Field(default=3, ge=0)
    request_timeout: float = Field(default=30, gt=0)
    router_temperature: float = Field(default=0.0, ge=0, le=2)
    router_max_tokens: int = Field(default=96, gt=0)
    router_reasoning_effort: str | None = "none"
    circuit_failure_threshold: int = Field(default=3, ge=1)
    circuit_open_seconds: float = Field(default=30, gt=0)
    circuit_half_open_probes: int = Field(default=1, ge=1)
    providers_json: str = "[]"

    def provider_configs(self) -> list[ProviderConfig]:
        """Parse the configured provider registry without exposing it to routers."""
        try:
            raw = json.loads(self.providers_json)
            configs: list[ProviderConfig] = []
            for item in raw:
                backend = str(item["backend"])
                configs.append(
                    ProviderConfig.model_validate(
                        {
                            **item,
                            "endpoint": self._chat_endpoint(backend),
                            "api_key": self._api_key(backend),
                            "timeout": self.request_timeout,
                        }
                    )
                )
            self._validate_provider_configs(configs)
            return configs
        except (json.JSONDecodeError, ValidationError, TypeError, KeyError) as exc:
            raise ValueError("PROVIDERS_JSON must be a valid provider configuration array") from exc

    def primary_router_config(self) -> RouterBackendConfig:
        return self._router_config(self.primary_router_backend, self.primary_router_model)

    def fallback_router_config(self) -> RouterBackendConfig:
        return self._router_config(self.fallback_router_backend, self.fallback_router_model)

    def _router_config(self, backend: str | None, model: str | None) -> RouterBackendConfig:
        if not backend or not model:
            raise ValueError("Router backend and model must be configured")
        return RouterBackendConfig(
            backend=backend,
            endpoint=self._chat_endpoint(backend),
            model=model,
            api_key=self._api_key(backend),
            timeout=self.request_timeout,
            temperature=self.router_temperature,
            max_tokens=self.router_max_tokens,
            reasoning_effort=self.router_reasoning_effort,
        )

    def _chat_endpoint(self, backend: str) -> str:
        endpoint = {
            "google": self.google_endpoint,
            "nvidia": self.nvidia_endpoint,
            "openrouter": self.openrouter_endpoint,
            "groq": self.groq_endpoint,
        }.get(backend)
        if endpoint:
            return endpoint
        # Compatibility for the pre-migration local .env, which configured base URLs.
        base_url = {
            "google": self.google_base_url,
            "nvidia": self.nvidia_base_url,
            "openrouter": self.openrouter_base_url,
            "groq": self.groq_base_url,
        }.get(backend)
        if not base_url:
            raise ValueError(f"No endpoint configured for backend '{backend}'")
        return f"{base_url.rstrip('/')}/chat/completions"

    def _api_key(self, backend: str) -> SecretStr:
        api_key = {
            "google": self.google_api_key,
            "nvidia": self.nvidia_api_key,
            "openrouter": self.openrouter_api_key,
            "groq": self.groq_api_key,
        }.get(backend)
        if api_key is None or not api_key.get_secret_value():
            raise ValueError(f"No API key configured for backend '{backend}'")
        return api_key

    @staticmethod
    def _validate_provider_configs(configs: list[ProviderConfig]) -> None:
        ids = [provider.id for provider in configs]
        if len(ids) != len(set(ids)):
            raise ValueError("Provider IDs must be unique")
        priorities = [(provider.task_group, provider.priority) for provider in configs]
        if len(priorities) != len(set(priorities)):
            raise ValueError("Provider priorities must be unique within each task group")
        enabled_groups = {provider.task_group for provider in configs if provider.enabled}
        missing = set(TaskGroup) - enabled_groups
        if missing:
            raise ValueError(f"Every task group requires an enabled provider: {sorted(missing)}")
        for provider in configs:
            if (
                provider.task_group == TaskGroup.MULTIMODAL
                and InputModality.IMAGE not in provider.capabilities.input_modalities
            ):
                raise ValueError("Multimodal providers must support image input")


@lru_cache
def get_settings() -> Settings:
    return Settings()

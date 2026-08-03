"""Externally supplied runtime settings for the proof of concept."""

from __future__ import annotations

import json
from functools import lru_cache

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from schemas.models import ProviderConfig


class Settings(BaseSettings):
    """Configuration loaded once at application startup from environment or ``.env``."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    primary_router_url: str | None = None
    primary_router_model: str | None = None
    fallback_router_url: str | None = None
    fallback_router_model: str | None = None
    max_retries: int = Field(default=3, ge=0)
    request_timeout: float = Field(default=30, gt=0)
    providers_json: str = "[]"

    def provider_configs(self) -> list[ProviderConfig]:
        """Parse the configured provider registry without exposing it to routers."""
        try:
            raw = json.loads(self.providers_json)
            return [ProviderConfig.model_validate(item) for item in raw]
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise ValueError("PROVIDERS_JSON must be a valid provider configuration array") from exc


@lru_cache
def get_settings() -> Settings:
    return Settings()

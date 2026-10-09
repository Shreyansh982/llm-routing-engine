"""Provider-aware model catalog loading for startup verification and health checks."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import httpx
from pydantic import SecretStr


@dataclass(frozen=True)
class ModelVerificationTarget:
    name: str
    backend: str
    endpoint: str
    model: str
    api_key: SecretStr
    timeout: float
    role: str = "provider"


@dataclass(frozen=True)
class ModelVerificationResult:
    name: str
    backend: str
    configured_model: str
    available: bool
    reason: str
    catalog_model: str | None = None
    http_status: int | None = None
    role: str = "provider"


class ModelCatalogError(RuntimeError):
    def __init__(self, reason: str, http_status: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status


def models_endpoint(chat_endpoint: str) -> str:
    suffix = "/chat/completions"
    if not chat_endpoint.endswith(suffix):
        raise ModelCatalogError("invalid_models_endpoint")
    return f"{chat_endpoint.removesuffix(suffix)}/models"


def resolve_catalog_model_id(
    backend: str, configured_model: str, catalog_ids: set[str] | frozenset[str]
) -> str | None:
    if configured_model in catalog_ids:
        return configured_model
    if backend == "google":
        google_id = f"models/{configured_model.removeprefix('models/')}"
        if google_id in catalog_ids:
            return google_id
    return None


async def load_model_catalog(target: ModelVerificationTarget) -> frozenset[str]:
    try:
        async with httpx.AsyncClient(timeout=target.timeout) as client:
            response = await client.get(
                models_endpoint(target.endpoint),
                headers={"Authorization": f"Bearer {target.api_key.get_secret_value()}"},
            )
            response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise ModelCatalogError("backend_timeout") from exc
    except httpx.NetworkError as exc:
        raise ModelCatalogError("backend_unreachable") from exc
    except httpx.HTTPStatusError as exc:
        raise ModelCatalogError("models_request_failed", exc.response.status_code) from exc

    try:
        payload = response.json()
        data = payload["data"]
        if not isinstance(data, list):
            raise TypeError
        if not data:
            raise ModelCatalogError("catalog_empty")
        if any(
            not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]
            for item in data
        ):
            raise TypeError
        return frozenset(item["id"] for item in data)
    except ModelCatalogError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise ModelCatalogError("malformed_catalog") from exc


class StartupModelVerifier:
    """Load one catalog per backend and verify every configured model against it."""

    def __init__(self, targets: list[ModelVerificationTarget]) -> None:
        self._targets = targets

    async def verify(self, role: str | None = None) -> list[ModelVerificationResult]:
        targets_by_backend: dict[str, list[ModelVerificationTarget]] = defaultdict(list)
        for target in self._targets:
            if role is not None and target.role != role:
                continue
            targets_by_backend[target.backend].append(target)

        results: list[ModelVerificationResult] = []
        for backend, targets in targets_by_backend.items():
            representative = targets[0]
            if any(
                target.endpoint != representative.endpoint
                or target.api_key.get_secret_value()
                != representative.api_key.get_secret_value()
                for target in targets[1:]
            ):
                results.extend(self._failed(target, "inconsistent_backend_configuration") for target in targets)
                continue
            try:
                catalog_ids = await load_model_catalog(representative)
            except ModelCatalogError as exc:
                results.extend(
                    self._failed(target, exc.reason, http_status=exc.http_status)
                    for target in targets
                )
                continue
            for target in targets:
                catalog_model = resolve_catalog_model_id(
                    target.backend, target.model, catalog_ids
                )
                results.append(
                    ModelVerificationResult(
                        name=target.name,
                        backend=target.backend,
                        configured_model=target.model,
                        available=catalog_model is not None,
                        reason="available" if catalog_model is not None else "model_not_found",
                        catalog_model=catalog_model,
                        role=target.role,
                    )
                )
        return results

    @staticmethod
    def _failed(
        target: ModelVerificationTarget,
        reason: str,
        *,
        http_status: int | None = None,
    ) -> ModelVerificationResult:
        return ModelVerificationResult(
            name=target.name,
            backend=target.backend,
            configured_model=target.model,
            available=False,
            reason=reason,
            http_status=http_status,
            role=target.role,
        )

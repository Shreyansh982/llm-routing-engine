"""Tenant resolution at the V2 presentation boundary."""

from __future__ import annotations

from fastapi import HTTPException, Request
from pydantic import ValidationError

from schemas.models import TenantContext


class TenantContextResolver:
    """Resolve the additive V2 tenant header without changing V1 authentication semantics."""

    header_name = "X-Tenant-ID"

    def resolve(self, request: Request) -> TenantContext:
        tenant_id = request.headers.get(self.header_name, "default").strip() or "default"
        try:
            return TenantContext(tenant_id=tenant_id)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="Invalid tenant context") from exc

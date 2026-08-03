"""Deterministic response normalization at the client boundary."""

from __future__ import annotations

import re

from schemas.models import ProviderResponse


class ResponseGateway:
    def __init__(self, identity_terms: list[str] | None = None) -> None:
        self._identity_terms = [term for term in (identity_terms or []) if term]

    def process(self, response: ProviderResponse) -> ProviderResponse:
        text = response.response.strip()
        for term in self._identity_terms:
            escaped = re.escape(term)
            text = re.sub(rf"\b[Aa]s\s+{escaped}\s*,?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(
                rf"\b[Ii]\s+am\s+{escaped}\b", "I am an AI assistant", text, flags=re.IGNORECASE
            )
            text = re.sub(escaped, "AI assistant", text, flags=re.IGNORECASE)
        # Strip obvious internal metadata labels without attempting impossible perfect masking.
        text = re.sub(r"(?im)^\s*(provider|model|endpoint|api[_ -]?key|routing)\s*:\s*.*$", "", text)
        text = re.sub(r"(?m)^[ \t]+", "", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return ProviderResponse(response=text)

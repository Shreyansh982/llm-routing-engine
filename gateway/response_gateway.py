"""Deterministic response normalization at the client boundary."""

from __future__ import annotations

import re

from schemas.models import ProviderResponse


class ResponseGateway:
    def __init__(self, identity_terms: list[str] | None = None) -> None:
        self._identity_terms = [term for term in (identity_terms or []) if term]

    def process(self, response: ProviderResponse) -> ProviderResponse:
        introduction = (
            r"((?:^ {0,3}(?:[-*+] |\d+[.)] |#{1,6} )?|(?<=[.!?])[ \t]+)"
            r"(?:(?:Hello|Hi|Hey)[,!]?[ \t]+)?(?:\*\*|__)?)"
        )
        identity_end = r"(?=[ \t]*(?:[.!?;,]|and[ \t]+I\b|(?:\*\*|__)?[ \t]*$))"
        assistant_description = r"an?[ \t]+(?:AI(?:[ \t]+(?:assistant|model))?|(?:large[ \t]+)?language[ \t]+model|assistant)"
        origin = r"(?:trained|developed|created)[ \t]+by[ \t]+"
        lines = []
        fence = None
        for line in response.response.splitlines(keepends=True):
            marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
            if fence is not None:
                lines.append(line)
                if re.fullmatch(rf" {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*\r?\n?", line):
                    fence = None
                continue
            if marker:
                fence = marker.group(1)
                lines.append(line)
                continue
            if not line.startswith(("    ", "\t")):
                for term in sorted(self._identity_terms, key=len, reverse=True):
                    name = re.escape(term) + r"(?:-V\d+(?:\.\d+)*)?(?![\w-])"
                    line = re.sub(
                        rf"{introduction}I(?:[ \t]+(?:am|was)|['’]m)[ \t]+(?:{assistant_description}[ \t]+)?{origin}{name}{identity_end}",
                        r"\1I am an AI assistant", line, flags=re.IGNORECASE,
                    )
                    line = re.sub(
                        rf"{introduction}(?:I[ \t]+am|I'm|I’m|My[ \t]+name[ \t]+is)[ \t]+{name}{identity_end}",
                        r"\1I am an AI assistant", line, flags=re.IGNORECASE,
                    )
                    line = re.sub(
                        rf"{introduction}As[ \t]+(?:{assistant_description}[ \t]+{origin})?{name},(?=[ \t]+(?:I\b|my\b))",
                        r"\1As an AI assistant,", line, flags=re.IGNORECASE,
                    )
            lines.append(line)
        return ProviderResponse(response="".join(lines))

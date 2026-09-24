from __future__ import annotations

import re

from .models import PlatformAttempt

_HEADER_PATTERNS = (
    re.compile(r"(?im)^(authorization\s*:\s*)(?:bearer\s+)?[^\r\n]+"),
    re.compile(r"(?im)^(cookie\s*:\s*)[^\r\n]+"),
    re.compile(r"(?im)^(set-cookie\s*:\s*)[^\r\n]+"),
)
_KEY_PATTERN = re.compile(r"\b(?:sk|pk|api)[-_][A-Za-z0-9_-]{8,}\b", re.IGNORECASE)


def sanitize_diagnostic(text: str, secret_values: list[str] | None = None) -> str:
    safe = text
    for pattern in _HEADER_PATTERNS:
        safe = pattern.sub(r"\1[REDACTED]", safe)
    safe = _KEY_PATTERN.sub("[REDACTED]", safe)
    for secret in secret_values or []:
        if secret:
            safe = safe.replace(secret, "[REDACTED]")
    return safe


def sanitize_attempt(
    attempt: PlatformAttempt, secret_values: list[str] | None = None
) -> PlatformAttempt:
    if not attempt.diagnostic:
        return attempt
    return attempt.model_copy(
        update={"diagnostic": sanitize_diagnostic(attempt.diagnostic, secret_values)}
    )

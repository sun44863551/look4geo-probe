from __future__ import annotations

import re

from .models import JobStatus, PlatformAttempt, ProbeRequest, QualityStatus

NON_ASCII_HYPHEN_URL_RE = re.compile(
    r"https?://[^\s<>]*[\u2010-\u2015][^\s<>]*", re.IGNORECASE
)


def assess_attempt_quality(
    attempt: PlatformAttempt, request: ProbeRequest
) -> PlatformAttempt:
    if attempt.status != JobStatus.SUCCEEDED:
        return attempt

    flags: list[str] = []
    if NON_ASCII_HYPHEN_URL_RE.search(attempt.raw_answer):
        flags.append("non_ascii_url_hyphen")

    configured = request.options.get("expected_terms", [])
    expected_terms = (
        [term.strip() for term in configured if isinstance(term, str) and term.strip()]
        if isinstance(configured, list)
        else []
    )
    if expected_terms and not any(
        term.casefold() in attempt.raw_answer.casefold() for term in expected_terms
    ):
        flags.append("expected_terms_missing")
        status = QualityStatus.FAILED
    elif flags:
        status = QualityStatus.REVIEW_REQUIRED
    elif expected_terms:
        status = QualityStatus.PASSED
    else:
        status = QualityStatus.NOT_CHECKED

    return attempt.model_copy(
        update={"quality_status": status, "quality_flags": flags}
    )

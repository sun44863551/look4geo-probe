from look4geo_probe.models import JobStatus, PlatformAttempt, ProbeRequest, QualityStatus
from look4geo_probe.quality import assess_attempt_quality


def attempt(answer: str) -> PlatformAttempt:
    return PlatformAttempt(
        platform="doubao",
        adapter="browser_skill",
        status=JobStatus.SUCCEEDED,
        raw_answer=answer,
    )


def test_unicode_hyphen_inside_visible_url_requires_review_without_failing_answer():
    original = attempt("来源：https://example.com/products/125572‑95‑4")

    assessed = assess_attempt_quality(original, ProbeRequest(prompt="research"))

    assert assessed.status == JobStatus.SUCCEEDED
    assert assessed.quality_status == QualityStatus.REVIEW_REQUIRED
    assert assessed.quality_flags == ["non_ascii_url_hyphen"]


def test_expected_terms_can_fail_business_quality_without_failing_transport():
    original = attempt("CAS 125572-95-4 对应 (R)-2-(4-羟基苯氧基)丙酸")
    request = ProbeRequest(
        prompt="research",
        options={"expected_terms": ["DCTA", "环己二胺四乙酸"]},
    )

    assessed = assess_attempt_quality(original, request)

    assert assessed.status == JobStatus.SUCCEEDED
    assert assessed.quality_status == QualityStatus.FAILED
    assert assessed.quality_flags == ["expected_terms_missing"]


def test_matching_one_expected_alias_passes_quality_review():
    original = attempt("该产品是 DCTA monohydrate")
    request = ProbeRequest(
        prompt="research", options={"expected_terms": ["DCTA", "环己二胺四乙酸"]}
    )

    assessed = assess_attempt_quality(original, request)

    assert assessed.quality_status == QualityStatus.PASSED
    assert assessed.quality_flags == []

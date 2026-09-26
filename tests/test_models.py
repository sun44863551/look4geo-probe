import pytest
from pydantic import ValidationError

from look4geo_probe.models import (
    JobStatus,
    PlatformAttempt,
    ProbeRequest,
    ProbeResult,
    RouteMode,
    SourceCaptureStatus,
    SourceEvidenceOrigin,
    SourceRecord,
    SourceRole,
)


def test_blank_prompt_is_rejected():
    with pytest.raises(ValidationError):
        ProbeRequest(prompt="   ")


def test_manual_mode_requires_platforms():
    with pytest.raises(ValidationError):
        ProbeRequest(prompt="test", mode=RouteMode.MANUAL)


def test_manual_mode_deduplicates_platforms_preserving_order():
    request = ProbeRequest(
        prompt="test", mode=RouteMode.MANUAL, platforms=["qwen", "qwen", "chatgpt"]
    )
    assert request.platforms == ["qwen", "chatgpt"]


def test_new_results_use_schema_version_two():
    result = ProbeResult(job_id="job-1", prompt="test", status="succeeded")
    assert result.schema_version == 2


def test_source_record_serializes_browser_visible_evidence():
    source = SourceRecord(
        url="https://example.com/product",
        title="Product specification",
        domain="example.com",
        snippet="Purity and packaging details",
        source_role=SourceRole.CITED,
        evidence_origin=SourceEvidenceOrigin.ANSWER_DOM,
        linked_in_answer=True,
    )

    assert source.model_dump(mode="json") == {
        "url": "https://example.com/product",
        "title": "Product specification",
        "domain": "example.com",
        "snippet": "Purity and packaging details",
        "source_role": "cited",
        "evidence_origin": "answer_dom",
        "linked_in_answer": True,
    }


def test_platform_attempt_defaults_to_no_exposed_sources():
    attempt = PlatformAttempt(
        platform="deepseek",
        adapter="browser_skill",
        status=JobStatus.SUCCEEDED,
    )

    assert attempt.sources == []
    assert attempt.source_capture_status == SourceCaptureStatus.NONE_EXPOSED
    assert attempt.source_capture_diagnostic is None


def test_schema_version_one_payload_remains_readable():
    result = ProbeResult.model_validate(
        {
            "schema_version": 1,
            "job_id": "old-job",
            "prompt": "legacy",
            "status": "succeeded",
            "attempts": [
                {
                    "platform": "deepseek",
                    "adapter": "browser_skill",
                    "status": "succeeded",
                }
            ],
        }
    )

    assert result.schema_version == 1
    assert result.attempts[0].sources == []
    assert result.attempts[0].source_capture_status == SourceCaptureStatus.NONE_EXPOSED


def test_repeat_count_must_be_between_one_and_ten():
    assert ProbeRequest(prompt="test", repeats=3).repeats == 3
    with pytest.raises(ValidationError):
        ProbeRequest(prompt="test", repeats=0)

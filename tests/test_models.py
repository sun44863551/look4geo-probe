import pytest
from pydantic import ValidationError

from look4geo_probe.models import ProbeRequest, ProbeResult, RouteMode


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


def test_result_schema_is_version_one():
    result = ProbeResult(job_id="job-1", prompt="test", status="succeeded")
    assert result.schema_version == 1

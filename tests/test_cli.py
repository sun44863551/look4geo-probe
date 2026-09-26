import json

from typer.testing import CliRunner

from look4geo_probe.cli import app, set_service_factory
from look4geo_probe.models import (
    Citation,
    JobStatus,
    PlatformAttempt,
    ProbeResult,
    SourceCaptureStatus,
    SourceEvidenceOrigin,
    SourceRecord,
    SourceRole,
)


class FakeService:
    last_request = None

    async def run(self, request):
        self.last_request = request
        return {"job_id": "job-1", "selected_platforms": ["qwen"], "reasons": {}, "status": JobStatus.RUNNING}

    async def wait(self, job_id):
        return None

    def result(self, job_id):
        return ProbeResult(
            job_id=job_id,
            prompt="hello",
            status=JobStatus.SUCCEEDED,
            attempts=[
                PlatformAttempt(
                    platform="qwen",
                    adapter="browser_skill",
                    status=JobStatus.SUCCEEDED,
                    raw_answer="answer",
                    citations=[Citation(url="https://example.com/source")],
                    sources=[
                        SourceRecord(
                            url="https://example.com/source",
                            domain="example.com",
                            source_role=SourceRole.CITED,
                            evidence_origin=SourceEvidenceOrigin.ANSWER_DOM,
                            linked_in_answer=True,
                        )
                    ],
                    source_capture_status=SourceCaptureStatus.CAPTURED,
                )
            ],
        )

    def status(self, job_id):
        return type("Job", (), {"job_id": job_id, "status": JobStatus.RUNNING, "diagnostic": None})()

    async def platforms(self):
        return {"qwen": {"available": True, "adapter": "ai_search_hub"}}

    async def login(self, platform):
        return {"platform": platform, "status": "user_action_required"}


def setup_function():
    set_service_factory(lambda: FakeService())


def test_cli_run_waits_and_prints_result_json():
    result = CliRunner().invoke(app, ["run", "hello", "--mode", "auto", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["job_id"] == "job-1"
    assert payload["status"] == "succeeded"


def test_cli_platforms_uses_same_service_interface():
    result = CliRunner().invoke(app, ["platforms", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["qwen"]["available"] is True


def test_cli_run_accepts_repeat_count():
    service = FakeService()
    set_service_factory(lambda: service)
    result = CliRunner().invoke(app, ["run", "hello", "--repeats", "3", "--json"])
    assert result.exit_code == 0
    assert service.last_request.repeats == 3


def test_cli_manual_platform_flags_are_unchanged():
    service = FakeService()
    set_service_factory(lambda: service)
    result = CliRunner().invoke(
        app,
        ["run", "hello", "--mode", "manual", "--platform", "qwen", "--json"],
    )
    assert result.exit_code == 0
    assert service.last_request.mode.value == "manual"
    assert service.last_request.platforms == ["qwen"]


def test_cli_json_keeps_answer_and_source_capture_status_separate():
    result = CliRunner().invoke(app, ["run", "hello", "--json"])
    attempt = json.loads(result.stdout)["attempts"][0]
    assert attempt["status"] == "succeeded"
    assert attempt["citations"] == [{"url": "https://example.com/source", "label": None}]
    assert attempt["sources"][0]["source_role"] == "cited"
    assert attempt["source_capture_status"] == "captured"
    assert attempt["source_capture_diagnostic"] is None

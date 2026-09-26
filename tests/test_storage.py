import json
from pathlib import Path

from look4geo_probe.models import (
    JobStatus,
    PlatformAttempt,
    ProbeRequest,
    ProbeResult,
    SourceCaptureStatus,
    SourceEvidenceOrigin,
    SourceRecord,
    SourceRole,
)
from look4geo_probe.storage import ProbeStore


def make_store(tmp_path: Path) -> ProbeStore:
    return ProbeStore(tmp_path / "probe.sqlite3", tmp_path / "runs")


def test_duplicate_requests_get_distinct_jobs(tmp_path):
    store = make_store(tmp_path)
    request = ProbeRequest(prompt="same prompt")
    first = store.create_job(request)
    second = store.create_job(request)
    assert first.job_id != second.job_id
    assert first.artifact_dir != second.artifact_dir
    assert first.artifact_dir.exists()
    assert second.artifact_dir.exists()


def test_recover_orphaned_running_job_preserves_artifacts(tmp_path):
    store = make_store(tmp_path)
    job = store.create_job(ProbeRequest(prompt="recover me"))
    marker = job.artifact_dir / "partial.txt"
    marker.write_text("evidence", encoding="utf-8")
    store.update_status(job.job_id, JobStatus.RUNNING)

    recovered = store.recover_orphans()

    assert job.job_id in recovered
    restored = store.get_job(job.job_id)
    assert restored.status == JobStatus.FAILED
    assert "interrupted" in (restored.diagnostic or "")
    assert marker.read_text(encoding="utf-8") == "evidence"


def test_request_round_trip_preserves_mode_and_platforms(tmp_path):
    store = make_store(tmp_path)
    original = ProbeRequest(prompt="question", mode="manual", platforms=["qwen"])
    job = store.create_job(original)
    assert store.get_job(job.job_id).request == original


def test_schema_version_one_result_loads_source_defaults(tmp_path):
    store = make_store(tmp_path)
    job = store.create_job(ProbeRequest(prompt="legacy"))
    legacy_payload = {
        "schema_version": 1,
        "job_id": job.job_id,
        "prompt": "legacy",
        "status": "succeeded",
        "attempts": [
            {
                "platform": "deepseek",
                "adapter": "browser_skill",
                "status": "succeeded",
                "raw_answer": "legacy answer",
            }
        ],
    }
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO results (job_id, result_json) VALUES (?, ?)",
            (job.job_id, json.dumps(legacy_payload)),
        )

    restored = store.get_result(job.job_id)

    assert restored.schema_version == 1
    assert restored.attempts[0].sources == []
    assert restored.attempts[0].source_capture_status == SourceCaptureStatus.NONE_EXPOSED


def test_schema_version_two_source_result_round_trips(tmp_path):
    store = make_store(tmp_path)
    job = store.create_job(ProbeRequest(prompt="current"))
    source = SourceRecord(
        url="https://example.com/specification",
        title="Specification",
        domain="example.com",
        snippet="Product evidence",
        source_role=SourceRole.SURFACED,
        evidence_origin=SourceEvidenceOrigin.SOURCE_PANEL,
        linked_in_answer=False,
    )
    result = ProbeResult(
        job_id=job.job_id,
        prompt="current",
        status=JobStatus.SUCCEEDED,
        attempts=[
            PlatformAttempt(
                platform="deepseek",
                adapter="browser_skill",
                status=JobStatus.SUCCEEDED,
                sources=[source],
                source_capture_status=SourceCaptureStatus.CAPTURED,
            )
        ],
    )

    store.save_result(result)

    assert store.get_result(job.job_id) == result

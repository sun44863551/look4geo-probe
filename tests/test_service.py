import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from look4geo_probe.models import (
    JobStatus,
    PlatformAttempt,
    PlatformHealth,
    ProbeRequest,
    QualityStatus,
    RoutingDecision,
)
from look4geo_probe.service import ProbeService
from look4geo_probe.storage import ProbeStore


class FixedRouter:
    def __init__(self, platforms):
        self.platforms = platforms

    def route(self, request, health):
        selected = [name for name in self.platforms if health[name].available]
        return RoutingDecision(selected_platforms=selected, reasons={name: ["test"] for name in selected})


class ControlledAdapter:
    def __init__(self, platform, gate=None, status=JobStatus.SUCCEEDED):
        self.platform = platform
        self.gate = gate
        self.status = status

    async def health(self, platform):
        return type("Health", (), {"ok": True, "detail": "ok"})()

    async def run(self, platform, request):
        if self.gate:
            await self.gate.wait()
        return PlatformAttempt(
            platform=platform,
            adapter="fake",
            status=self.status,
            raw_answer=f"answer:{platform}",
        )


class ConcurrencyRecordingAdapter(ControlledAdapter):
    def __init__(self, platform):
        super().__init__(platform)
        self.active = 0
        self.max_active = 0

    async def run(self, platform, request):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0)
        result = await super().run(platform, request)
        self.active -= 1
        return result


class TimingAdapter(ControlledAdapter):
    def __init__(self, platform):
        super().__init__(platform)
        self.entered_at = None

    async def run(self, platform, request):
        self.entered_at = datetime.now(timezone.utc)
        await asyncio.sleep(0)
        return await super().run(platform, request)


@pytest.mark.asyncio
async def test_run_returns_job_before_adapter_completes(tmp_path: Path):
    gate = asyncio.Event()
    store = ProbeStore(tmp_path / "db.sqlite3", tmp_path / "runs")
    service = ProbeService(
        FixedRouter(["chatgpt"]), store, {"chatgpt": ControlledAdapter("chatgpt", gate)}
    )
    submission = await service.run(ProbeRequest(prompt="hello"))
    assert submission["status"] == JobStatus.QUEUED
    assert service.status(submission["job_id"]).status == JobStatus.QUEUED
    gate.set()
    await service.wait(submission["job_id"])
    assert service.result(submission["job_id"]).status == JobStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_partial_success_preserves_successful_attempt(tmp_path: Path):
    store = ProbeStore(tmp_path / "db.sqlite3", tmp_path / "runs")
    service = ProbeService(
        FixedRouter(["baidu", "gemini"]),
        store,
        {
            "baidu": ControlledAdapter("baidu"),
            "gemini": ControlledAdapter("gemini", status=JobStatus.FAILED),
        },
    )
    submission = await service.run(ProbeRequest(prompt="compare"))
    await service.wait(submission["job_id"])
    result = service.result(submission["job_id"])
    assert result.status == JobStatus.PARTIAL
    assert [a.platform for a in result.attempts if a.status == JobStatus.SUCCEEDED] == ["baidu"]


@pytest.mark.asyncio
async def test_repeats_create_independent_numbered_attempts(tmp_path: Path):
    store = ProbeStore(tmp_path / "db.sqlite3", tmp_path / "runs")
    service = ProbeService(
        FixedRouter(["chatgpt"]), store, {"chatgpt": ControlledAdapter("chatgpt")}
    )
    submission = await service.run(ProbeRequest(prompt="repeat", repeats=3))
    await service.wait(submission["job_id"])
    attempts = service.result(submission["job_id"]).attempts
    assert [attempt.sample_index for attempt in attempts] == [1, 2, 3]


@pytest.mark.asyncio
async def test_repeats_for_one_platform_run_serially(tmp_path: Path):
    store = ProbeStore(tmp_path / "db.sqlite3", tmp_path / "runs")
    adapter = ConcurrencyRecordingAdapter("chatgpt")
    service = ProbeService(FixedRouter(["chatgpt"]), store, {"chatgpt": adapter})

    submission = await service.run(ProbeRequest(prompt="repeat", repeats=3))
    await service.wait(submission["job_id"])

    assert adapter.max_active == 1


@pytest.mark.asyncio
async def test_platforms_sharing_browser_runtime_run_serially(tmp_path: Path):
    store = ProbeStore(tmp_path / "db.sqlite3", tmp_path / "runs")
    adapter = ConcurrencyRecordingAdapter("shared-browser")
    service = ProbeService(
        FixedRouter(["baidu", "gemini"]),
        store,
        {"baidu": adapter, "gemini": adapter},
    )

    submission = await service.run(ProbeRequest(prompt="compare", repeats=2))
    await service.wait(submission["job_id"])

    assert adapter.max_active == 1


@pytest.mark.asyncio
async def test_parallel_jobs_and_service_instances_wait_in_queue(tmp_path):
    store = ProbeStore(tmp_path / "db.sqlite3", tmp_path / "runs")
    gate = asyncio.Event()
    adapter = ControlledAdapter("baidu", gate)
    first = ProbeService(FixedRouter(["baidu"]), store, {"baidu": adapter})
    second = ProbeService(FixedRouter(["baidu"]), store, {"baidu": adapter})
    one = await first.run(ProbeRequest(prompt="first"))
    await asyncio.sleep(0.02)
    two = await second.run(ProbeRequest(prompt="second"))
    await asyncio.sleep(0.02)
    assert first.status(one["job_id"]).status == JobStatus.RUNNING
    assert second.status(two["job_id"]).status == JobStatus.QUEUED
    gate.set()
    await asyncio.gather(first.wait(one["job_id"]), second.wait(two["job_id"]))
    assert second.result(two["job_id"]).status == JobStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_unexpected_adapter_error_does_not_leave_job_running(tmp_path):
    class BrokenAdapter(ControlledAdapter):
        async def run(self, *args):
            raise RuntimeError("adapter crashed")

    store = ProbeStore(tmp_path / "db.sqlite3", tmp_path / "runs")
    service = ProbeService(FixedRouter(["baidu"]), store, {"baidu": BrokenAdapter("baidu")})
    job = await service.run(ProbeRequest(prompt="crash"))
    await service.wait(job["job_id"])
    assert service.status(job["job_id"]).status == JobStatus.FAILED
    assert "adapter crashed" in service.result(job["job_id"]).diagnostic


@pytest.mark.asyncio
async def test_health_error_does_not_leave_job_routing(tmp_path):
    class BrokenHealth(ControlledAdapter):
        async def health(self, *args):
            raise RuntimeError("health crashed")

    store = ProbeStore(tmp_path / "db.sqlite3", tmp_path / "runs")
    service = ProbeService(FixedRouter(["baidu"]), store, {"baidu": BrokenHealth("baidu")})
    job = await service.run(ProbeRequest(prompt="health crash"))
    assert service.status(job["job_id"]).status == JobStatus.FAILED
    assert "health crashed" in service.result(job["job_id"]).diagnostic


@pytest.mark.asyncio
async def test_cancelled_queued_job_does_not_interrupt_current_job(tmp_path):
    gate = asyncio.Event()
    store = ProbeStore(tmp_path / "db.sqlite3", tmp_path / "runs")
    service = ProbeService(FixedRouter(["baidu"]), store, {"baidu": ControlledAdapter("baidu", gate)})
    first = await service.run(ProbeRequest(prompt="first"))
    await asyncio.sleep(0.02)
    second = await service.run(ProbeRequest(prompt="queued"))
    await asyncio.sleep(0.02)
    task = service._tasks[second["job_id"]]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "while queued" in service.result(second["job_id"]).diagnostic
    assert service.status(first["job_id"]).status == JobStatus.RUNNING
    gate.set()
    await service.wait(first["job_id"])
    assert service.result(first["job_id"]).status == JobStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_service_records_finished_time_for_every_attempt(tmp_path: Path):
    store = ProbeStore(tmp_path / "db.sqlite3", tmp_path / "runs")
    adapter = TimingAdapter("chatgpt")
    service = ProbeService(
        FixedRouter(["chatgpt"]), store, {"chatgpt": adapter}
    )

    submission = await service.run(ProbeRequest(prompt="timing"))
    await service.wait(submission["job_id"])

    attempt = service.result(submission["job_id"]).attempts[0]
    assert attempt.started_at <= adapter.entered_at
    assert attempt.finished_at is not None
    assert attempt.finished_at >= adapter.entered_at


@pytest.mark.asyncio
async def test_service_applies_expected_term_quality_review(tmp_path: Path):
    store = ProbeStore(tmp_path / "db.sqlite3", tmp_path / "runs")
    service = ProbeService(
        FixedRouter(["doubao"]), store, {"doubao": ControlledAdapter("doubao")}
    )

    submission = await service.run(
        ProbeRequest(prompt="research", options={"expected_terms": ["DCTA"]})
    )
    await service.wait(submission["job_id"])

    attempt = service.result(submission["job_id"]).attempts[0]
    assert attempt.status == JobStatus.SUCCEEDED
    assert attempt.quality_status == QualityStatus.FAILED
    assert attempt.quality_flags == ["expected_terms_missing"]


@pytest.mark.asyncio
async def test_cancelled_execution_is_persisted_as_failed_not_left_running(tmp_path: Path):
    gate = asyncio.Event()
    store = ProbeStore(tmp_path / "db.sqlite3", tmp_path / "runs")
    service = ProbeService(
        FixedRouter(["chatgpt"]),
        store,
        {"chatgpt": ControlledAdapter("chatgpt", gate)},
    )
    submission = await service.run(ProbeRequest(prompt="cancel me"))
    task = service._tasks[submission["job_id"]]
    await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    job = service.status(submission["job_id"])
    result = service.result(submission["job_id"])
    assert job.status == JobStatus.FAILED
    assert "cancelled" in (job.diagnostic or "")
    assert result.status == JobStatus.FAILED
    assert "cancelled" in (result.diagnostic or "")

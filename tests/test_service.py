import asyncio
from pathlib import Path

import pytest

from look4geo_probe.models import (
    JobStatus,
    PlatformAttempt,
    PlatformHealth,
    ProbeRequest,
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


@pytest.mark.asyncio
async def test_run_returns_job_before_adapter_completes(tmp_path: Path):
    gate = asyncio.Event()
    store = ProbeStore(tmp_path / "db.sqlite3", tmp_path / "runs")
    service = ProbeService(
        FixedRouter(["chatgpt"]), store, {"chatgpt": ControlledAdapter("chatgpt", gate)}
    )
    submission = await service.run(ProbeRequest(prompt="hello"))
    assert submission["status"] == JobStatus.RUNNING
    assert service.status(submission["job_id"]).status == JobStatus.RUNNING
    gate.set()
    await service.wait(submission["job_id"])
    assert service.result(submission["job_id"]).status == JobStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_partial_success_preserves_successful_attempt(tmp_path: Path):
    store = ProbeStore(tmp_path / "db.sqlite3", tmp_path / "runs")
    service = ProbeService(
        FixedRouter(["qwen", "gemini"]),
        store,
        {
            "qwen": ControlledAdapter("qwen"),
            "gemini": ControlledAdapter("gemini", status=JobStatus.FAILED),
        },
    )
    submission = await service.run(ProbeRequest(prompt="compare"))
    await service.wait(submission["job_id"])
    result = service.result(submission["job_id"])
    assert result.status == JobStatus.PARTIAL
    assert [a.platform for a in result.attempts if a.status == JobStatus.SUCCEEDED] == ["qwen"]


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
        FixedRouter(["qwen", "gemini"]),
        store,
        {"qwen": adapter, "gemini": adapter},
    )

    submission = await service.run(ProbeRequest(prompt="compare", repeats=2))
    await service.wait(submission["job_id"])

    assert adapter.max_active == 1

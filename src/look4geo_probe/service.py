from __future__ import annotations

import asyncio

from .jobs import JobManager
from .models import (
    JobStatus,
    PlatformHealth,
    ProbeRequest,
    ProbeResult,
)
from .storage import ProbeStore, StoredJob


class ProbeService:
    def __init__(self, router, store: ProbeStore, adapters: dict[str, object]):
        self.router = router
        self.store = store
        self.jobs = JobManager(store)
        self.adapters = adapters
        self._tasks: dict[str, asyncio.Task] = {}

    async def run(self, request: ProbeRequest) -> dict:
        job = self.jobs.submit(request)
        self.jobs.transition(job.job_id, JobStatus.ROUTING)
        health: dict[str, PlatformHealth] = {}
        for platform, adapter in self.adapters.items():
            adapter_health = await adapter.health(platform)
            health[platform] = PlatformHealth(
                available=bool(adapter_health.ok), reason=adapter_health.detail or None
            )
        routing = self.router.route(request, health)
        if not routing.selected_platforms:
            result = ProbeResult(
                job_id=job.job_id,
                prompt=request.prompt,
                status=JobStatus.FAILED,
                routing=routing,
                diagnostic="no healthy platform selected",
            )
            self.store.save_result(result)
            self.jobs.transition(job.job_id, JobStatus.FAILED, result.diagnostic)
            return {
                "job_id": job.job_id,
                "selected_platforms": [],
                "reasons": routing.reasons,
                "status": JobStatus.FAILED,
            }
        self.jobs.transition(job.job_id, JobStatus.RUNNING)
        task = asyncio.create_task(self._execute(job.job_id, request, routing))
        self._tasks[job.job_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(job.job_id, None))
        return {
            "job_id": job.job_id,
            "selected_platforms": routing.selected_platforms,
            "reasons": routing.reasons,
            "status": JobStatus.RUNNING,
        }

    async def _execute(self, job_id: str, request: ProbeRequest, routing) -> None:
        scheduled = [
            (name, sample_index)
            for name in routing.selected_platforms
            for sample_index in range(1, request.repeats + 1)
        ]
        raw_attempts = await asyncio.gather(
            *(self.adapters[name].run(name, request) for name, _ in scheduled)
        )
        attempts = [
            attempt.model_copy(update={"sample_index": sample_index})
            for attempt, (_, sample_index) in zip(raw_attempts, scheduled, strict=True)
        ]
        successes = sum(attempt.status == JobStatus.SUCCEEDED for attempt in attempts)
        waiting = any(attempt.status == JobStatus.WAITING_FOR_LOGIN for attempt in attempts)
        if successes == len(attempts):
            status = JobStatus.SUCCEEDED
        elif successes:
            status = JobStatus.PARTIAL
        elif waiting:
            status = JobStatus.WAITING_FOR_LOGIN
        else:
            status = JobStatus.FAILED
        result = ProbeResult(
            job_id=job_id,
            prompt=request.prompt,
            status=status,
            routing=routing,
            attempts=list(attempts),
        )
        self.store.save_result(result)
        self.jobs.transition(job_id, status)

    def status(self, job_id: str) -> StoredJob:
        return self.jobs.status(job_id)

    def result(self, job_id: str) -> ProbeResult:
        return self.store.get_result(job_id)

    async def wait(self, job_id: str) -> None:
        task = self._tasks.get(job_id)
        if task is not None:
            await task

    async def platforms(self) -> dict[str, dict]:
        output = {}
        for platform, adapter in self.adapters.items():
            health = await adapter.health(platform)
            output[platform] = {
                "adapter": adapter.name,
                "available": health.ok,
                "detail": health.detail,
            }
        return output

    async def login(self, platform: str) -> dict:
        if platform not in self.adapters:
            raise KeyError(platform)
        return await self.adapters[platform].login(platform)

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from .jobs import JobManager
from .browser_lock import BrowserOperationLock
from .models import (
    FailureKind,
    JobStatus,
    PlatformHealth,
    ProbeRequest,
    ProbeResult,
)
from .storage import ProbeStore, StoredJob
from .quality import assess_attempt_quality


class ProbeService:
    def __init__(self, router, store: ProbeStore, adapters: dict[str, object]):
        self.router = router
        self.store = store
        self.jobs = JobManager(store)
        self.adapters = adapters
        self._tasks: dict[str, asyncio.Task] = {}
        # Queue whole jobs across CLI processes and MCP service instances. The
        # adapter lock alone only protects individual browser operations and
        # cannot express queued jobs or protect other adapter runtimes.
        self.execution_lock = BrowserOperationLock(
            store.database_path.parent / ".probe-jobs.lock"
        )

    async def run(self, request: ProbeRequest) -> dict:
        job = self.jobs.submit(request)
        self.jobs.transition(job.job_id, JobStatus.ROUTING)
        health: dict[str, PlatformHealth] = {}
        try:
            for platform, adapter in self.adapters.items():
                adapter_health = await adapter.health(platform)
                health[platform] = PlatformHealth(
                    available=bool(adapter_health.ok), reason=adapter_health.detail or None
                )
            routing = self.router.route(request, health)
        except Exception as error:
            diagnostic = f"routing error: {type(error).__name__}: {error}"
            self.store.save_result(ProbeResult(
                job_id=job.job_id, prompt=request.prompt,
                buyer_baseline=request.buyer_baseline,
                buyer_locale=request.buyer_baseline.locale,
                status=JobStatus.FAILED, diagnostic=diagnostic,
            ))
            self.jobs.transition(job.job_id, JobStatus.FAILED, diagnostic)
            return dict(
                job_id=job.job_id, selected_platforms=[], reasons={},
                status=JobStatus.FAILED, diagnostic=diagnostic,
            )
        if not routing.selected_platforms:
            result = ProbeResult(
                job_id=job.job_id,
                prompt=request.prompt,
                buyer_baseline=request.buyer_baseline,
                buyer_locale=request.buyer_baseline.locale,
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
        self.jobs.transition(job.job_id, JobStatus.QUEUED, "queued: browser sampling runs serially")
        task = asyncio.create_task(self._execute(job.job_id, request, routing))
        self._tasks[job.job_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(job.job_id, None))
        return {
            "job_id": job.job_id,
            "selected_platforms": routing.selected_platforms,
            "reasons": routing.reasons,
            "status": JobStatus.QUEUED,
        }

    async def _execute(self, job_id: str, request: ProbeRequest, routing) -> None:
        try:
            async with self.execution_lock:
                self.jobs.transition(job_id, JobStatus.RUNNING)
                await self._execute_serial(job_id, request, routing)
        except asyncio.CancelledError:
            if self.status(job_id).status == JobStatus.QUEUED:
                diagnostic = "cancelled: worker stopped while queued; no browser sample started"
                self.store.save_result(ProbeResult(
                    job_id=job_id, prompt=request.prompt,
                    buyer_baseline=request.buyer_baseline,
                    buyer_locale=request.buyer_baseline.locale,
                    status=JobStatus.FAILED, routing=routing, diagnostic=diagnostic,
                ))
                self.jobs.transition(job_id, JobStatus.FAILED, diagnostic)
            raise

    async def _execute_serial(self, job_id: str, request: ProbeRequest, routing) -> None:
        attempts = []
        try:
            for name in routing.selected_platforms:
                for sample_index in range(1, request.repeats + 1):
                    started_at = datetime.now(timezone.utc)
                    attempt = await self.adapters[name].run(name, request)
                    attempt = assess_attempt_quality(attempt, request)
                    attempts.append(
                        attempt.model_copy(
                            update={
                                "sample_index": sample_index,
                                "buyer_baseline": request.buyer_baseline,
                                "buyer_locale": request.buyer_baseline.locale,
                                "started_at": started_at,
                                "finished_at": attempt.finished_at or datetime.now(timezone.utc),
                            }
                        )
                    )
                    if attempt.failure == FailureKind.RATE_LIMITED:
                        break  # Do not spend remaining repeats against a confirmed wall.
        except (asyncio.CancelledError, Exception) as error:
            cancelled = isinstance(error, asyncio.CancelledError)
            diagnostic = (
                "cancelled: worker stopped before completion" if cancelled
                else f"worker error: {type(error).__name__}: {error}"
            )
            result = ProbeResult(
                job_id=job_id,
                prompt=request.prompt,
                buyer_baseline=request.buyer_baseline,
                buyer_locale=request.buyer_baseline.locale,
                status=JobStatus.FAILED,
                routing=routing,
                attempts=list(attempts),
                diagnostic=diagnostic,
            )
            self.store.save_result(result)
            self.jobs.transition(job_id, JobStatus.FAILED, diagnostic)
            if cancelled:
                raise
            return
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
            buyer_baseline=request.buyer_baseline,
            buyer_locale=request.buyer_baseline.locale,
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

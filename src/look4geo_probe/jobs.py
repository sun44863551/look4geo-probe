from __future__ import annotations

from .models import JobStatus, ProbeRequest
from .storage import ProbeStore, StoredJob


class InvalidTransition(ValueError):
    pass


ALLOWED_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.QUEUED: {JobStatus.ROUTING, JobStatus.RUNNING, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.ROUTING: {JobStatus.RUNNING, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.RUNNING: {
        JobStatus.WAITING_FOR_LOGIN,
        JobStatus.PARTIAL,
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.WAITING_FOR_LOGIN: {JobStatus.RUNNING, JobStatus.PARTIAL, JobStatus.FAILED},
    JobStatus.PARTIAL: set(),
    JobStatus.SUCCEEDED: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELLED: set(),
}


class JobManager:
    def __init__(self, store: ProbeStore):
        self.store = store

    def submit(self, request: ProbeRequest) -> StoredJob:
        return self.store.create_job(request)

    def status(self, job_id: str) -> StoredJob:
        return self.store.get_job(job_id)

    def transition(
        self, job_id: str, target: JobStatus, diagnostic: str | None = None
    ) -> StoredJob:
        current = self.store.get_job(job_id)
        if target not in ALLOWED_TRANSITIONS[current.status]:
            raise InvalidTransition(f"{current.status.value} -> {target.value}")
        return self.store.update_status(job_id, target, diagnostic)

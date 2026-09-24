from pathlib import Path

import pytest

from look4geo_probe.jobs import InvalidTransition, JobManager
from look4geo_probe.models import JobStatus, ProbeRequest
from look4geo_probe.storage import ProbeStore


def test_job_manager_enforces_allowed_transition(tmp_path: Path):
    manager = JobManager(ProbeStore(tmp_path / "db.sqlite3", tmp_path / "runs"))
    job = manager.submit(ProbeRequest(prompt="hello"))
    manager.transition(job.job_id, JobStatus.ROUTING)
    manager.transition(job.job_id, JobStatus.RUNNING)
    manager.transition(job.job_id, JobStatus.SUCCEEDED)
    assert manager.status(job.job_id).status == JobStatus.SUCCEEDED


def test_terminal_job_cannot_return_to_running(tmp_path: Path):
    manager = JobManager(ProbeStore(tmp_path / "db.sqlite3", tmp_path / "runs"))
    job = manager.submit(ProbeRequest(prompt="hello"))
    manager.transition(job.job_id, JobStatus.FAILED)
    with pytest.raises(InvalidTransition):
        manager.transition(job.job_id, JobStatus.RUNNING)

import pytest

from look4geo_probe.models import ProbeRequest
from look4geo_probe.runtime import build_service


def test_run_root_keeps_jobs_and_locks_in_separate_lanes(tmp_path, monkeypatch):
    monkeypatch.setenv('LOOK4GEO_BROWSER_ID', 'test-browser')
    monkeypatch.setenv('LOOK4GEO_CAMOUFOX_ENABLED', '0')
    services = []
    for lane in ('scotle', 'originature'):
        lane_root = tmp_path / lane
        monkeypatch.setenv('LOOK4GEO_RUN_ROOT', str(lane_root))
        service = build_service()
        assert service.store.database_path == lane_root / 'data/probe.sqlite3'
        job = service.store.create_job(ProbeRequest(prompt=lane))
        assert job.artifact_dir.parent == lane_root / 'data/runs'
        assert service.execution_lock.path == lane_root / 'data/.probe-jobs.lock'
        assert service.adapters['perplexity'].adapters[0].operation_lock.path == lane_root / 'data/.browser_skill.lock'
        services.append((service, job))
    with pytest.raises(KeyError):
        services[1][0].store.get_job(services[0][1].job_id)

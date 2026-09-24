import json

from typer.testing import CliRunner

from look4geo_probe.cli import app, set_service_factory
from look4geo_probe.models import JobStatus, ProbeResult


class FakeService:
    last_request = None

    async def run(self, request):
        self.last_request = request
        return {"job_id": "job-1", "selected_platforms": ["qwen"], "reasons": {}, "status": JobStatus.RUNNING}

    async def wait(self, job_id):
        return None

    def result(self, job_id):
        return ProbeResult(job_id=job_id, prompt="hello", status=JobStatus.SUCCEEDED)

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

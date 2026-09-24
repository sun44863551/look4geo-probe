import pytest

from look4geo_probe.mcp_server import ProbeMcpTools
from look4geo_probe.models import JobStatus, ProbeResult


class FakeService:
    async def run(self, request):
        return {"job_id": "job-2", "selected_platforms": ["perplexity"], "reasons": {"perplexity": ["citation"]}, "status": JobStatus.RUNNING}

    def status(self, job_id):
        return type("Job", (), {"job_id": job_id, "status": JobStatus.RUNNING, "diagnostic": None})()

    def result(self, job_id):
        return ProbeResult(job_id=job_id, prompt="source?", status=JobStatus.SUCCEEDED)

    async def platforms(self):
        return {"perplexity": {"available": True}}

    async def login(self, platform):
        return {"platform": platform, "status": "user_action_required"}


@pytest.mark.asyncio
async def test_mcp_run_returns_immediately_without_waiting():
    tools = ProbeMcpTools(FakeService())
    result = await tools.probe_run("source?", mode="auto")
    assert result["job_id"] == "job-2"
    assert result["status"] == "running"


@pytest.mark.asyncio
async def test_mcp_result_is_structured_json_data():
    tools = ProbeMcpTools(FakeService())
    result = await tools.probe_result("job-2")
    assert result["schema_version"] == 1
    assert result["status"] == "succeeded"

from pathlib import Path
import pytest
from test_browser_skill_adapter import DelayedAnswerClient, FakeBrowserClient
from test_service import FixedRouter, ControlledAdapter
from look4geo_probe.adapters.browser_skill import page_is_rate_limited, BrowserSkillAdapter, BrowserProbeOutput
from look4geo_probe.models import FailureKind, JobStatus, ProbeRequest, PlatformAttempt
from look4geo_probe.service import ProbeService
from look4geo_probe.storage import ProbeStore


@pytest.mark.parametrize("text", ["Too many requests. We have temporarily restricted access to your conversation history.", "请求过于频繁。我们已暂时限制你访问对话记录。", "问答过频限制"])
def test_history_limit_is_recognized(text):
    assert page_is_rate_limited({"page_text": text})


@pytest.mark.asyncio
async def test_history_limit_keeps_only_new_answer():
    client = DelayedAnswerClient([
        {"answers": ["old answer"], "links": []},
        {"answers": ["old answer", "new answer for question"], "links": [], "page_text": "请求过于频繁"},
    ])
    output = await client.probe("session", "chatgpt", "question", .01)
    assert output.failure == FailureKind.RATE_LIMITED
    assert output.answer == "new answer for question"


@pytest.mark.asyncio
async def test_rate_limit_stops_repeats(tmp_path):
    class Adapter(ControlledAdapter):
        async def run(self, platform, request):
            return PlatformAttempt(platform=platform, adapter="fake", status=JobStatus.FAILED, failure=FailureKind.RATE_LIMITED)
    service = ProbeService(FixedRouter(["chatgpt"]), ProbeStore(tmp_path / "db", tmp_path / "runs"), {"chatgpt": Adapter("chatgpt")})
    job = await service.run(ProbeRequest(prompt="q", repeats=3))
    await service.wait(job["job_id"])
    assert len(service.result(job["job_id"]).attempts) == 1


@pytest.mark.asyncio
async def test_cooldown_survives_new_adapter_and_preserves_evidence(tmp_path):
    first = BrowserSkillAdapter(client=FakeBrowserClient(output=BrowserProbeOutput(answer="visible answer", failure=FailureKind.RATE_LIMITED)), artifact_root=tmp_path)
    result = await first.run("chatgpt", ProbeRequest(prompt="q"))
    assert result.raw_answer == "visible answer"
    second_client = FakeBrowserClient(output=BrowserProbeOutput(answer="must not submit"))
    second = BrowserSkillAdapter(client=second_client, artifact_root=tmp_path)
    result = await second.run("chatgpt", ProbeRequest(prompt="q"))
    assert result.failure == FailureKind.RATE_LIMITED
    assert not second_client.started

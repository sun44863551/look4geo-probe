from pathlib import Path

import pytest

from look4geo_probe.adapters.browser_skill import BrowserProbeOutput, BrowserSkillAdapter
from look4geo_probe.models import JobStatus, ProbeRequest


class FakeBrowserClient:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
        self.started = []
        self.stopped = []

    async def start(self, platform):
        self.started.append(platform)
        return "session-1"

    async def probe(self, session_id, platform, prompt, timeout):
        if self.error:
            raise self.error
        return self.output

    async def stop(self, session_id):
        self.stopped.append(session_id)


@pytest.mark.asyncio
async def test_browser_adapter_returns_answer_and_stops_session(tmp_path: Path):
    client = FakeBrowserClient(
        BrowserProbeOutput(answer="回答内容", citations=["https://example.com/source"])
    )
    adapter = BrowserSkillAdapter(client=client, artifact_root=tmp_path)
    attempt = await adapter.run("chatgpt", ProbeRequest(prompt="测试"))
    assert attempt.status == JobStatus.SUCCEEDED
    assert attempt.raw_answer == "回答内容"
    assert attempt.citations[0].url == "https://example.com/source"
    assert client.stopped == ["session-1"]


@pytest.mark.asyncio
async def test_browser_adapter_reports_login_and_stops_session(tmp_path: Path):
    client = FakeBrowserClient(BrowserProbeOutput(login_required=True))
    adapter = BrowserSkillAdapter(client=client, artifact_root=tmp_path)
    attempt = await adapter.run("deepseek", ProbeRequest(prompt="测试"))
    assert attempt.status == JobStatus.WAITING_FOR_LOGIN
    assert client.stopped == ["session-1"]


@pytest.mark.asyncio
async def test_browser_adapter_stops_session_after_error(tmp_path: Path):
    client = FakeBrowserClient(error=RuntimeError("selector changed"))
    adapter = BrowserSkillAdapter(client=client, artifact_root=tmp_path)
    attempt = await adapter.run("perplexity", ProbeRequest(prompt="测试"))
    assert attempt.status == JobStatus.FAILED
    assert "selector changed" in (attempt.diagnostic or "")
    assert client.stopped == ["session-1"]


@pytest.mark.asyncio
async def test_browser_adapter_rejects_unknown_platform_without_session(tmp_path: Path):
    client = FakeBrowserClient()
    adapter = BrowserSkillAdapter(client=client, artifact_root=tmp_path)
    attempt = await adapter.run("unknown", ProbeRequest(prompt="测试"))
    assert attempt.status == JobStatus.FAILED
    assert client.started == []

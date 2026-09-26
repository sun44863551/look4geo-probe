import asyncio
from pathlib import Path

import pytest

from look4geo_probe.adapters.ai_search_hub import AIHubAdapter, CommandResult
from look4geo_probe.models import (
    JobStatus,
    ProbeRequest,
    SourceCaptureStatus,
    SourceEvidenceOrigin,
    SourceRole,
)


class FakeRunner:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.commands = []

    async def run(self, command, timeout):
        self.commands.append((command, timeout))
        if self.error:
            raise self.error
        return self.result


@pytest.mark.asyncio
async def test_ai_hub_returns_saved_answer_and_uses_argument_array(tmp_path: Path):
    output = tmp_path / "answer.txt"
    output.write_text("完整回答\nhttps://example.com/source", encoding="utf-8")
    runner = FakeRunner(CommandResult(0, "done", ""))
    adapter = AIHubAdapter(tmp_path, runner=runner, python_executable="python-test")

    attempt = await adapter.run(
        "qwen", ProbeRequest(prompt="测试问题", options={"output": str(output)})
    )

    assert attempt.status == JobStatus.SUCCEEDED
    assert attempt.raw_answer.startswith("完整回答")
    assert len(attempt.sources) == 1
    assert attempt.sources[0].url == "https://example.com/source"
    assert attempt.sources[0].source_role == SourceRole.CITED
    assert attempt.sources[0].evidence_origin == SourceEvidenceOrigin.ANSWER_DOM
    assert attempt.sources[0].linked_in_answer is True
    assert attempt.source_capture_status == SourceCaptureStatus.CAPTURED
    assert [citation.url for citation in attempt.citations] == [
        "https://example.com/source"
    ]
    command, _ = runner.commands[0]
    assert command[:3] == ["python-test", str(tmp_path / "scripts/run_web_chat.py"), "--site"]
    assert command[4:6] == ["--prompt", "测试问题"]


@pytest.mark.asyncio
async def test_ai_hub_classifies_login_requirement(tmp_path: Path):
    runner = FakeRunner(CommandResult(2, "", "Login required; finish login in browser"))
    adapter = AIHubAdapter(tmp_path, runner=runner)
    attempt = await adapter.run("doubao", ProbeRequest(prompt="test"))
    assert attempt.status == JobStatus.WAITING_FOR_LOGIN
    assert "login" in (attempt.diagnostic or "").lower()


@pytest.mark.asyncio
async def test_ai_hub_timeout_is_failed_attempt(tmp_path: Path):
    runner = FakeRunner(error=asyncio.TimeoutError())
    adapter = AIHubAdapter(tmp_path, runner=runner)
    attempt = await adapter.run("gemini", ProbeRequest(prompt="test"))
    assert attempt.status == JobStatus.FAILED
    assert attempt.diagnostic == "timeout"
    assert attempt.sources == []


@pytest.mark.asyncio
async def test_ai_hub_answer_without_urls_is_none_exposed(tmp_path: Path):
    output = tmp_path / "answer.txt"
    output.write_text("没有展示外部信源的完整回答", encoding="utf-8")
    runner = FakeRunner(CommandResult(0, "done", ""))
    adapter = AIHubAdapter(tmp_path, runner=runner)

    attempt = await adapter.run(
        "qwen", ProbeRequest(prompt="测试问题", options={"output": str(output)})
    )

    assert attempt.status == JobStatus.SUCCEEDED
    assert attempt.sources == []
    assert attempt.citations == []
    assert attempt.source_capture_status == SourceCaptureStatus.NONE_EXPOSED

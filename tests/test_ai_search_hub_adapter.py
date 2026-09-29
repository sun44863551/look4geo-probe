import asyncio
from pathlib import Path

import pytest

from look4geo_probe.adapters.ai_search_hub import AIHubAdapter, CommandResult
from look4geo_probe.models import (
    FailureKind,
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
        "gemini", ProbeRequest(prompt="测试问题", options={"output": str(output)})
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
    assert attempt.failure == FailureKind.TIMEOUT
    assert attempt.diagnostic == "timeout"
    assert attempt.sources == []


@pytest.mark.asyncio
async def test_ai_hub_answer_without_urls_is_none_exposed(tmp_path: Path):
    output = tmp_path / "answer.txt"
    output.write_text(
        "这是一个已经完成的回答，页面没有展示任何外部信源，但正文内容仍然有效。",
        encoding="utf-8",
    )
    runner = FakeRunner(CommandResult(0, "done", ""))
    adapter = AIHubAdapter(tmp_path, runner=runner)

    attempt = await adapter.run(
        "gemini", ProbeRequest(prompt="测试问题", options={"output": str(output)})
    )

    assert attempt.status == JobStatus.SUCCEEDED
    assert attempt.sources == []
    assert attempt.citations == []
    assert attempt.source_capture_status == SourceCaptureStatus.NONE_EXPOSED


@pytest.mark.asyncio
async def test_ai_hub_rejects_landing_page_placeholder_as_success(tmp_path: Path):
    output = tmp_path / "answer.txt"
    output.write_text("有什么可以帮你的吗？\n自动", encoding="utf-8")
    adapter = AIHubAdapter(
        tmp_path, runner=FakeRunner(CommandResult(0, "done", ""))
    )

    attempt = await adapter.run(
        "gemini", ProbeRequest(prompt="请查找供应商", options={"output": str(output)})
    )

    assert attempt.status == JobStatus.FAILED
    assert attempt.failure.value == "extraction_failed"
    assert "valid answer" in (attempt.diagnostic or "")


@pytest.mark.asyncio
async def test_ai_hub_rejects_doubao_deep_search_preamble_wrapped_in_page_text(
    tmp_path: Path,
):
    output = tmp_path / "answer.txt"
    output.write_text(
        "GEO优化服务招标技术标与评分标准撰写\n"
        "AI 生成可能有误 请核实\n"
        "想招标采购 GEO 优化服务，技术标和评分标准应该怎么写？\n"
        "搜索 1 个关键词，参考 10 篇资料\n"
        "我将结合 GEO 地理优化服务的行业特性，整理一套可直接落地、标准化的技术标模板。\n"
        "对话\n视频生成\n音乐生成\n录音转写\n豆包 快速",
        encoding="utf-8",
    )
    adapter = AIHubAdapter(
        tmp_path, runner=FakeRunner(CommandResult(0, "done", ""))
    )

    attempt = await adapter.run(
        "doubao", ProbeRequest(prompt="想招标采购GEO优化服务", options={"output": str(output)})
    )

    assert attempt.status == JobStatus.FAILED
    assert attempt.failure == FailureKind.EXTRACTION_FAILED


@pytest.mark.asyncio
async def test_ai_hub_uses_configured_default_timeout(tmp_path: Path):
    output = tmp_path / "answer.txt"
    output.write_text("这是一个足够长且有效的完整回答内容，用于验证默认超时时间。", encoding="utf-8")
    runner = FakeRunner(CommandResult(0, "done", ""))
    adapter = AIHubAdapter(tmp_path, runner=runner, default_timeout=180)

    await adapter.run(
        "gemini", ProbeRequest(prompt="测试问题", options={"output": str(output)})
    )

    assert runner.commands[0][1] == 180

from pathlib import Path

import pytest

from look4geo_probe.adapters.browser_skill import (
    BrowserProbeOutput,
    BrowserSkillAdapter,
    normalize_for_browser,
    submission_confirmed,
    select_main_answer,
)
from look4geo_probe.models import FailureKind, JobStatus, ProbeRequest


def test_normalize_for_browser_preserves_original_and_records_changes():
    normalized = normalize_for_browser("purity ≥ 99% — supplier’s certificate\u00a0required")
    assert normalized.original == "purity ≥ 99% — supplier’s certificate\u00a0required"
    assert normalized.sent == "purity >= 99% - supplier's certificate required"
    assert normalized.changed is True


def test_perplexity_selects_longest_answer_not_follow_up():
    answer = select_main_answer(
        "perplexity",
        ["Short follow-up?", "This is the complete researched answer with suppliers and evidence."],
    )
    assert answer == "This is the complete researched answer with suppliers and evidence."


@pytest.mark.parametrize(
    ("platform", "before", "after", "expected"),
    [
        ("chatgpt", "https://chatgpt.com/", "https://chatgpt.com/c/abc", True),
        ("perplexity", "https://www.perplexity.ai/", "https://www.perplexity.ai/", False),
        ("deepseek", "https://chat.deepseek.com/", "https://chat.deepseek.com/a/chat/s/abc", True),
    ],
)
def test_submission_confirmation_requires_new_conversation_url(
    platform, before, after, expected
):
    assert submission_confirmed(platform, before, after) is expected


class FakeBrowserClient:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
        self.started = []
        self.stopped = []
        self.last_prompt = None

    async def start(self, platform):
        self.started.append(platform)
        return "session-1"

    async def probe(self, session_id, platform, prompt, timeout):
        self.last_prompt = prompt
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
async def test_browser_adapter_classifies_rate_limit_without_counting_success(tmp_path: Path):
    client = FakeBrowserClient(
        BrowserProbeOutput(failure=FailureKind.RATE_LIMITED, diagnostic="HTTP 429")
    )
    adapter = BrowserSkillAdapter(client=client, artifact_root=tmp_path)
    attempt = await adapter.run("perplexity", ProbeRequest(prompt="测试"))
    assert attempt.status == JobStatus.FAILED
    assert attempt.failure == FailureKind.RATE_LIMITED
    assert attempt.raw_answer == ""


@pytest.mark.asyncio
async def test_browser_adapter_records_original_and_sent_prompt(tmp_path: Path):
    client = FakeBrowserClient(BrowserProbeOutput(answer="回答内容"))
    adapter = BrowserSkillAdapter(client=client, artifact_root=tmp_path)
    attempt = await adapter.run("chatgpt", ProbeRequest(prompt="纯度 ≥ 99%"))
    assert attempt.query_original == "纯度 ≥ 99%"
    assert attempt.query_sent == "纯度 >= 99%"
    assert attempt.query_normalized is True
    assert client.last_prompt == "纯度 >= 99%"


@pytest.mark.asyncio
async def test_browser_adapter_rejects_unknown_platform_without_session(tmp_path: Path):
    client = FakeBrowserClient()
    adapter = BrowserSkillAdapter(client=client, artifact_root=tmp_path)
    attempt = await adapter.run("unknown", ProbeRequest(prompt="测试"))
    assert attempt.status == JobStatus.FAILED
    assert client.started == []

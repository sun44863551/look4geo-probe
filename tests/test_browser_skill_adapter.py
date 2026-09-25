from pathlib import Path

import pytest

from look4geo_probe.adapters.browser_skill import (
    BskCliClient,
    BrowserProbeOutput,
    BrowserSkillAdapter,
    PLATFORMS,
    normalize_for_browser,
    submission_confirmed,
    select_main_answer,
    command_error_detail,
)
from look4geo_probe.models import FailureKind, JobStatus, ProbeRequest


def test_normalize_for_browser_preserves_original_and_records_changes():
    normalized = normalize_for_browser("purity ≥ 99% — supplier’s certificate\u00a0required")
    assert normalized.original == "purity ≥ 99% — supplier’s certificate\u00a0required"
    assert normalized.sent == "purity >= 99% - supplier's certificate required"
    assert normalized.changed is True


def test_browser_skill_defines_every_probe_platform():
    assert set(PLATFORMS) == {
        "doubao",
        "deepseek",
        "yuanbao",
        "qwen",
        "chatgpt",
        "gemini",
        "perplexity",
        "grok",
    }


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


def test_chatgpt_submission_is_confirmed_when_answer_starts_before_url_changes():
    assert submission_confirmed(
        "chatgpt",
        "https://chatgpt.com/",
        "https://chatgpt.com/",
        answer_started=True,
    ) is True


def test_textbox_lookup_accepts_current_perplexity_label():
    page = '@e18 textbox "问任何事情..." [empty]'
    assert BskCliClient._find_textbox_ref(
        page, ("输入 @ 以使用连接器", "问任何事情...")
    ) == "@e18"


def test_textbox_lookup_accepts_current_chatgpt_label():
    page = '@e17 textbox "询问 ChatGPT" [empty]'
    assert BskCliClient._find_textbox_ref(
        page, PLATFORMS["chatgpt"]["textbox"]
    ) == "@e17"


def test_bsk_error_detail_reads_json_message_from_stdout():
    stdout = b'{"code":"cdp_failed","message":"fill target changed"}'
    assert command_error_detail(stdout, b"") == "cdp_failed: fill target changed"


class FillFallbackClient(BskCliClient):
    def __init__(self):
        super().__init__("browser")
        self.calls = []

    async def _run_json(self, *args, timeout=30.0):
        self.calls.append(args)
        if args[0] == "fill" and str(args[1]).startswith("@"):
            raise RuntimeError("fill target changed or lost focus before typing")
        return {"value": True}


class ComposerStateClient(BskCliClient):
    def __init__(self, values):
        super().__init__("browser", poll_interval=0)
        self.values = iter(values)
        self.calls = []

    async def _run_json(self, *args, timeout=30.0):
        self.calls.append(args)
        return {"value": next(self.values)}


class LoginOverlayClient(BskCliClient):
    def __init__(self):
        super().__init__("browser", poll_interval=0)

    async def _run_json(self, *args, timeout=30.0):
        if args[0] == "navigate":
            return {"final_url": "https://yuanbao.tencent.com/chat/naQivTmsDa"}
        if args[0] == "observe":
            return {
                "text": 'StaticText "微信登录"\nStaticText "请使用微信扫描二维码登录"'
            }
        if args[0] == "evaluate":
            return {"value": True}
        return {"ok": True}


class DelayedAnswerClient(BskCliClient):
    def __init__(self, pages):
        super().__init__("browser", poll_interval=0)
        self.pages = iter(pages)
        self.last_page = {"answers": [], "links": []}
        self.submit_count = 0

    async def _run_json(self, *args, timeout=30.0):
        if args[0] == "navigate":
            return {"final_url": "https://www.perplexity.ai/"}
        if args[0] == "observe":
            return {"text": '@e18 textbox "问任何事情..." [empty]'}
        return {"value": True}

    async def _enter_prompt(self, *args, **kwargs):
        return None

    async def _wait_submission_ready(self, *args, **kwargs):
        return True

    async def _submit_prompt(self, *args, **kwargs):
        self.submit_count += 1

    async def _current_url(self, session_id):
        return "https://www.perplexity.ai/"

    async def _answer_page(self, session_id, selector):
        try:
            self.last_page = next(self.pages)
        except StopIteration:
            pass
        return self.last_page


@pytest.mark.asyncio
async def test_selector_only_composer_is_available_without_accessibility_ref():
    client = ComposerStateClient([True])

    assert await client._composer_available("session", 'div[contenteditable="true"]') is True


@pytest.mark.asyncio
async def test_delayed_composer_is_retried_until_available():
    client = ComposerStateClient([False, False, True])

    assert await client._wait_composer_available(
        "session", 'div[contenteditable="true"]', rounds=3
    ) is True
    assert len(client.calls) == 3


@pytest.mark.asyncio
async def test_login_overlay_wins_over_hidden_composer():
    client = LoginOverlayClient()

    output = await client.probe("session", "yuanbao", "hello", timeout=1)

    assert output.login_required is True
    assert output.failure is None


def test_doubao_answer_boundary_targets_message_content():
    assert PLATFORMS["doubao"]["answer_selector"] == '[data-testid="message_text_content"]'


def test_gemini_answer_boundary_excludes_prompt_and_navigation():
    assert PLATFORMS["gemini"]["answer_selector"] == "model-response-content message-content"


@pytest.mark.asyncio
async def test_disabled_send_control_times_out_without_submitting():
    client = ComposerStateClient(
        [{"found": True, "ready": False}, {"found": True, "ready": False}]
    )

    assert await client._wait_submission_ready("session", "chatgpt", rounds=2) is False
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_chatgpt_falls_back_to_native_insert_text_when_fill_target_changes():
    client = FillFallbackClient()
    await client._enter_prompt("session", "chatgpt", "@e42", "hello")
    commands = [call[0] for call in client.calls]
    assert commands == ["fill", "click", "press", "press", "evaluate"]
    assert "insertText" in client.calls[-1][1]


@pytest.mark.asyncio
async def test_chatgpt_submits_using_stable_composer_selector():
    client = FillFallbackClient()
    await client._submit_prompt("session", "chatgpt", "@e42")
    assert client.calls[-1][:4] == (
        "press",
        "Enter",
        "--selector",
        'div[contenteditable="true"]',
    )


@pytest.mark.asyncio
async def test_perplexity_uses_native_input_and_submit_button_on_fill_change():
    client = FillFallbackClient()
    await client._enter_prompt("session", "perplexity", "@e18", "hello")
    assert client.calls[-1][:2] == ("fill", "--selector")
    assert "--no-clear" in client.calls[-1]
    await client._submit_prompt("session", "perplexity", "@e18")
    assert client.calls[-1][:2] == ("click", 'button[aria-label="提交"], button[aria-label="Submit"]')


@pytest.mark.asyncio
async def test_slow_spa_response_is_submitted_once_and_detected_by_answer_delta():
    client = DelayedAnswerClient(
        [
            {"answers": ["old answer"], "links": []},
            {"answers": ["old answer"], "links": []},
            {"answers": ["old answer"], "links": []},
            {"answers": ["old answer", "new complete answer"], "links": []},
            {"answers": ["old answer", "new complete answer"], "links": []},
            {"answers": ["old answer", "new complete answer"], "links": []},
        ]
    )

    output = await client.probe("session", "perplexity", "question", timeout=1)

    assert client.submit_count == 1
    assert output.answer == "new complete answer"


@pytest.mark.asyncio
async def test_qwen_transient_controls_are_not_accepted_as_answers():
    client = DelayedAnswerClient(
        [
            {"answers": [], "links": []},
            {"answers": ["正在搜索网络\n跳过"], "links": []},
            {"answers": ["正在搜索网络\n跳过"], "links": []},
            {"answers": ["正在搜索网络\n跳过"], "links": []},
        ]
    )

    output = await client.probe("session", "qwen", "question", timeout=0.01)

    assert output.failure in {FailureKind.EXTRACTION_FAILED, FailureKind.TIMEOUT}
    assert output.answer == ""


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

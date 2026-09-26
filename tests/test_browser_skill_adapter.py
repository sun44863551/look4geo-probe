import json
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
from look4geo_probe.models import (
    FailureKind,
    JobStatus,
    ProbeRequest,
    SourceCaptureStatus,
    SourceEvidenceOrigin,
    SourceRecord,
    SourceRole,
)
from look4geo_probe.sources import citations_from_sources


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


def test_qwen_selects_full_answer_not_nested_tail_fragment():
    full_answer = "完整回答：" + ("供应商、纯度、MOQ、出口文件与来源。" * 20)
    answer = select_main_answer(
        "qwen",
        [full_answer, "供应商、纯度、MOQ。", "采购前请再次核验。"],
    )

    assert answer == full_answer


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


def test_textbox_lookup_accepts_current_qwen_label():
    page = '@e17 textbox "询问 Qwen" [empty]'
    assert BskCliClient._find_textbox_ref(
        page, PLATFORMS["qwen"]["textbox"]
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


class SourceCollectorClient(BskCliClient):
    def __init__(self, open_results, panel_pages=()):
        super().__init__("browser", poll_interval=0)
        self.open_results = iter(open_results)
        self.panel_pages = iter(panel_pages)
        self.last_panel_page = {"panel_found": True, "cards": []}
        self.open_calls = 0

    async def _open_source_panel(self, session_id, platform):
        self.open_calls += 1
        result = next(self.open_results)
        if isinstance(result, Exception):
            raise result
        return result

    async def _source_panel_page(self, session_id, platform):
        try:
            self.last_panel_page = next(self.panel_pages)
        except StopIteration:
            pass
        return self.last_panel_page


class SourceFailureProbeClient(DelayedAnswerClient):
    async def _collect_visible_sources(self, session_id, platform, answer_links):
        return [], SourceCaptureStatus.FAILED, "source panel blocked"


def configure_source_panel(monkeypatch, platform="chatgpt"):
    config = {
        **PLATFORMS[platform],
        "source_trigger_labels": ("Sources",),
        "source_trigger_selectors": ('button[data-testid="sources"]',),
        "source_panel_selectors": ('[role="dialog"]',),
        "source_card_selectors": ('a[data-testid="source-card"]',),
        "excluded_source_domains": ("chatgpt.com",),
    }
    monkeypatch.setitem(PLATFORMS, platform, config)


DOMESTIC_SOURCE_FIXTURES = Path(__file__).parent / "fixtures" / "source_dom"


def load_source_fixture(platform):
    return json.loads(
        (DOMESTIC_SOURCE_FIXTURES / f"{platform}.json").read_text(encoding="utf-8")
    )


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
async def test_chatgpt_submits_using_native_send_button():
    client = FillFallbackClient()
    await client._submit_prompt("session", "chatgpt", "@e42")
    assert client.calls[-1][:2] == (
        "click",
        'button[aria-label="发送"], button[aria-label="Send prompt"]',
    )


@pytest.mark.asyncio
async def test_qwen_submits_using_native_send_button():
    client = FillFallbackClient()

    await client._submit_prompt("session", "qwen", "@e17")

    assert client.calls[-1][:2] == (
        "click",
        'button[aria-label="发送"], button[aria-label="Send"]',
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


def test_browser_probe_output_defaults_to_no_exposed_sources():
    output = BrowserProbeOutput(answer="complete")

    assert output.sources == []
    assert output.source_capture_status == SourceCaptureStatus.NONE_EXPOSED
    assert output.source_capture_diagnostic is None


@pytest.mark.asyncio
async def test_answer_links_are_cited_answer_dom_sources(monkeypatch):
    configure_source_panel(monkeypatch)
    client = SourceCollectorClient([{"found": False, "opened": False}])

    sources, status, diagnostic = await client._collect_visible_sources(
        "session",
        "chatgpt",
        [{"url": "https://Example.com/spec?utm_source=chat", "title": "Spec"}],
    )

    assert len(sources) == 1
    assert sources[0].url == "https://example.com/spec"
    assert sources[0].source_role == SourceRole.CITED
    assert sources[0].evidence_origin == SourceEvidenceOrigin.ANSWER_DOM
    assert sources[0].linked_in_answer is True
    assert status == SourceCaptureStatus.CAPTURED
    assert diagnostic is None


@pytest.mark.asyncio
async def test_missing_source_trigger_without_answer_links_is_none_exposed(monkeypatch):
    configure_source_panel(monkeypatch)
    client = SourceCollectorClient([{"found": False, "opened": False}])

    sources, status, diagnostic = await client._collect_visible_sources(
        "session", "chatgpt", []
    )

    assert sources == []
    assert status == SourceCaptureStatus.NONE_EXPOSED
    assert diagnostic is None


@pytest.mark.asyncio
async def test_source_trigger_that_cannot_open_is_failed_without_answer_loss(monkeypatch):
    configure_source_panel(monkeypatch)
    client = SourceCollectorClient(
        [{"found": True, "opened": False, "diagnostic": "source panel blocked"}]
    )

    sources, status, diagnostic = await client._collect_visible_sources(
        "session",
        "chatgpt",
        [{"url": "https://example.com/cited", "title": "Cited"}],
    )

    assert [item.url for item in sources] == ["https://example.com/cited"]
    assert status == SourceCaptureStatus.FAILED
    assert diagnostic == "source panel blocked"


@pytest.mark.asyncio
async def test_source_collection_failure_preserves_stable_answer():
    client = SourceFailureProbeClient(
        [
            {"answers": [], "links": [], "answer_links": []},
            {"answers": ["complete answer"], "links": [], "answer_links": []},
            {"answers": ["complete answer"], "links": [], "answer_links": []},
            {"answers": ["complete answer"], "links": [], "answer_links": []},
        ]
    )

    output = await client.probe("session", "deepseek", "question", timeout=1)

    assert output.answer == "complete answer"
    assert output.failure is None
    assert output.source_capture_status == SourceCaptureStatus.FAILED
    assert output.source_capture_diagnostic == "source panel blocked"


@pytest.mark.asyncio
async def test_open_empty_source_panel_is_none_exposed(monkeypatch):
    configure_source_panel(monkeypatch)
    client = SourceCollectorClient(
        [{"found": True, "opened": True}],
        [
            {
                "panel_found": True,
                "cards": [{"url": "https://chatgpt.com/internal", "title": "Internal"}],
            },
            {
                "panel_found": True,
                "cards": [{"url": "https://chatgpt.com/internal", "title": "Internal"}],
            },
        ],
    )

    sources, status, diagnostic = await client._collect_visible_sources(
        "session", "chatgpt", []
    )

    assert sources == []
    assert status == SourceCaptureStatus.NONE_EXPOSED
    assert diagnostic is None


@pytest.mark.asyncio
async def test_stale_source_trigger_is_reobserved_only_once(monkeypatch):
    configure_source_panel(monkeypatch)
    client = SourceCollectorClient(
        [RuntimeError("stale element reference"), {"found": True, "opened": True}],
        [
            {
                "panel_found": True,
                "cards": [{"url": "https://example.com/result", "title": "Result"}],
            },
            {
                "panel_found": True,
                "cards": [{"url": "https://example.com/result", "title": "Result"}],
            },
        ],
    )

    sources, status, diagnostic = await client._collect_visible_sources(
        "session", "chatgpt", []
    )

    assert client.open_calls == 2
    assert sources[0].source_role == SourceRole.SURFACED
    assert status == SourceCaptureStatus.CAPTURED
    assert diagnostic is None


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["doubao", "deepseek", "yuanbao", "qwen"])
async def test_domestic_platforms_collect_cited_and_surfaced_fixture_sources(platform):
    fixture = load_source_fixture(platform)
    panel_page = {"panel_found": True, "cards": fixture["panel_cards"]}
    client = SourceCollectorClient(
        [{"found": True, "opened": True}], [panel_page, panel_page]
    )

    sources, status, diagnostic = await client._collect_visible_sources(
        "session", platform, fixture["answer_links"]
    )

    assert [source.url for source in sources] == [
        "https://evidence.example/spec?id=42",
        "https://market.example/supplier",
    ]
    assert [source.source_role for source in sources] == [
        SourceRole.CITED,
        SourceRole.SURFACED,
    ]
    assert sources[0].evidence_origin == SourceEvidenceOrigin.ANSWER_DOM
    assert status == SourceCaptureStatus.CAPTURED
    assert diagnostic is None


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["doubao", "deepseek", "yuanbao", "qwen"])
async def test_domestic_platforms_report_none_exposed_when_trigger_is_absent(platform):
    client = SourceCollectorClient([{"found": False, "opened": False}])

    sources, status, diagnostic = await client._collect_visible_sources(
        "session", platform, []
    )

    assert sources == []
    assert status == SourceCaptureStatus.NONE_EXPOSED
    assert diagnostic is None


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["doubao", "deepseek", "yuanbao", "qwen"])
async def test_domestic_platforms_keep_citations_when_panel_open_fails(platform):
    fixture = load_source_fixture(platform)
    client = SourceCollectorClient(
        [{"found": True, "opened": False, "diagnostic": "panel unavailable"}]
    )

    sources, status, diagnostic = await client._collect_visible_sources(
        "session", platform, fixture["answer_links"]
    )

    assert [source.source_role for source in sources] == [SourceRole.CITED]
    assert status == SourceCaptureStatus.FAILED
    assert diagnostic == "panel unavailable"


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "challenge_text",
    [
        "请确认你的年龄以继续\n你出生于哪一年？",
        "通过验证以确保正常访问。\n请拖动下方滑块完成验证。",
    ],
)
async def test_qwen_human_verification_returns_user_action_required(challenge_text):
    client = DelayedAnswerClient(
        [
            {"answers": [], "links": [], "page_text": ""},
            {"answers": [], "links": [], "page_text": challenge_text},
        ]
    )

    output = await client.probe("session", "qwen", "question", timeout=1)

    assert output.login_required is True
    assert output.diagnostic == "human verification required"


@pytest.mark.asyncio
async def test_grok_prompt_echo_is_ignored_until_real_answer_arrives():
    prompt = "Which suppliers have stock?"
    client = DelayedAnswerClient(
        [
            {"answers": [], "links": []},
            {"answers": [prompt], "links": []},
            {"answers": [prompt], "links": []},
            {"answers": [prompt], "links": []},
            {"answers": [prompt, "A real supplier answer with evidence."], "links": []},
            {"answers": [prompt, "A real supplier answer with evidence."], "links": []},
            {"answers": [prompt, "A real supplier answer with evidence."], "links": []},
        ]
    )

    output = await client.probe("session", "grok", prompt, timeout=1)

    assert output.answer == "A real supplier answer with evidence."


@pytest.mark.asyncio
async def test_chatgpt_does_not_finish_while_generation_is_running():
    preamble = "我会先核实生产商、库存与监管证据。"
    complete = preamble + "\n完整答案：" + ("供应商 A 有公开库存与监管证据。" * 20)
    client = DelayedAnswerClient(
        [
            {"answers": [], "links": [], "generating": False},
            {"answers": [preamble], "links": [], "generating": True},
            {"answers": [preamble], "links": [], "generating": True},
            {"answers": [preamble], "links": [], "generating": True},
            {"answers": [complete], "links": [], "generating": True},
            {"answers": [complete], "links": [], "generating": False},
            {"answers": [complete], "links": [], "generating": False},
            {"answers": [complete], "links": [], "generating": False},
        ]
    )

    output = await client.probe("session", "chatgpt", "请调查供应商", timeout=1)

    assert output.answer == complete


@pytest.mark.asyncio
async def test_chatgpt_short_preamble_is_not_success_without_generation_signal():
    preamble = "I’ll first verify the suppliers and regulatory evidence."
    client = DelayedAnswerClient(
        [
            {"answers": [], "links": []},
            {"answers": [preamble], "links": []},
            {"answers": [preamble], "links": []},
            {"answers": [preamble], "links": []},
        ]
    )

    output = await client.probe("session", "chatgpt", "Research suppliers", timeout=0.01)

    assert output.failure == FailureKind.EXTRACTION_FAILED
    assert output.answer == ""


@pytest.mark.asyncio
async def test_chatgpt_chinese_preamble_is_not_success():
    preamble = "我会把厂家身份、现货证据、价格分开核验，避免把平台报价误当成厂家库存。"
    client = DelayedAnswerClient(
        [
            {"answers": [], "links": []},
            {"answers": [preamble], "links": []},
            {"answers": [preamble], "links": []},
            {"answers": [preamble], "links": []},
        ]
    )

    output = await client.probe("session", "chatgpt", "Research suppliers", timeout=0.01)

    assert output.failure == FailureKind.EXTRACTION_FAILED


@pytest.mark.asyncio
async def test_chatgpt_project_context_contamination_is_not_success():
    contaminated = "主线进度：继续围绕 Look4GEO 的化工询盘能力做可验证证据测试。"
    client = DelayedAnswerClient(
        [
            {"answers": [], "links": []},
            {"answers": [contaminated], "links": []},
            {"answers": [contaminated], "links": []},
            {"answers": [contaminated], "links": []},
        ]
    )

    output = await client.probe("session", "chatgpt", "Research suppliers", timeout=0.01)

    assert output.failure == FailureKind.EXTRACTION_FAILED


@pytest.mark.asyncio
async def test_perplexity_quota_wall_is_rate_limited_not_timeout():
    client = DelayedAnswerClient(
        [
            {"answers": [], "links": [], "page_text": ""},
            {
                "answers": [],
                "links": [],
                "page_text": "您已达到免费搜索次数上限。你的使用权限将在几小时后重置。",
            },
        ]
    )

    output = await client.probe("session", "perplexity", "question", timeout=0.01)

    assert output.failure == FailureKind.RATE_LIMITED
    assert "quota" in (output.diagnostic or "")


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
    sources = [
        SourceRecord(
            url="https://example.com/source",
            title="Evidence",
            domain="example.com",
            snippet=None,
            source_role=SourceRole.CITED,
            evidence_origin=SourceEvidenceOrigin.ANSWER_DOM,
            linked_in_answer=True,
        ),
        SourceRecord(
            url="https://other.example/card",
            title="Related source",
            domain="other.example",
            snippet="Visible in source panel",
            source_role=SourceRole.SURFACED,
            evidence_origin=SourceEvidenceOrigin.SOURCE_PANEL,
            linked_in_answer=False,
        ),
    ]
    client = FakeBrowserClient(
        BrowserProbeOutput(
            answer="回答内容",
            citations=["https://wrong.example/legacy"],
            sources=sources,
            source_capture_status=SourceCaptureStatus.CAPTURED,
        )
    )
    adapter = BrowserSkillAdapter(client=client, artifact_root=tmp_path)
    attempt = await adapter.run("chatgpt", ProbeRequest(prompt="测试"))
    assert attempt.status == JobStatus.SUCCEEDED
    assert attempt.raw_answer == "回答内容"
    assert attempt.sources == sources
    assert attempt.citations == citations_from_sources(sources)
    assert attempt.source_capture_status == SourceCaptureStatus.CAPTURED
    assert attempt.source_capture_diagnostic is None
    assert client.stopped == ["session-1"]


@pytest.mark.asyncio
async def test_browser_adapter_keeps_answer_success_when_source_capture_failed(tmp_path: Path):
    client = FakeBrowserClient(
        BrowserProbeOutput(
            answer="回答内容",
            source_capture_status=SourceCaptureStatus.FAILED,
            source_capture_diagnostic="source panel changed",
        )
    )
    adapter = BrowserSkillAdapter(client=client, artifact_root=tmp_path)

    attempt = await adapter.run("chatgpt", ProbeRequest(prompt="测试"))

    assert attempt.status == JobStatus.SUCCEEDED
    assert attempt.raw_answer == "回答内容"
    assert attempt.source_capture_status == SourceCaptureStatus.FAILED
    assert attempt.source_capture_diagnostic == "source panel changed"


@pytest.mark.asyncio
async def test_browser_adapter_reports_login_and_stops_session(tmp_path: Path):
    client = FakeBrowserClient(BrowserProbeOutput(login_required=True))
    adapter = BrowserSkillAdapter(client=client, artifact_root=tmp_path)
    attempt = await adapter.run("deepseek", ProbeRequest(prompt="测试"))
    assert attempt.status == JobStatus.WAITING_FOR_LOGIN
    assert client.stopped == ["session-1"]


@pytest.mark.asyncio
async def test_browser_adapter_preserves_human_verification_diagnostic(tmp_path: Path):
    client = FakeBrowserClient(
        BrowserProbeOutput(
            login_required=True,
            diagnostic="human verification required",
        )
    )
    adapter = BrowserSkillAdapter(client=client, artifact_root=tmp_path)

    attempt = await adapter.run("qwen", ProbeRequest(prompt="测试"))

    assert attempt.status == JobStatus.WAITING_FOR_LOGIN
    assert attempt.failure == FailureKind.LOGIN_REQUIRED
    assert attempt.diagnostic == "human verification required"


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
    assert attempt.sources == []


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

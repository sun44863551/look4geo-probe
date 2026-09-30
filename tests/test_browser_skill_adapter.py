import json
from pathlib import Path

import pytest
import look4geo_probe.adapters.browser_skill as browser_skill

from look4geo_probe.adapters.browser_skill import (
    BskCliClient,
    BrowserProbeOutput,
    BrowserSkillAdapter,
    PLATFORMS,
    answer_links_for_text,
    extract_text_urls,
    normalize_for_browser,
    page_is_rate_limited,
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
        "baidu",
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


def test_grok_limit_reset_wall_is_rate_limited():
    assert page_is_rate_limited(
        {"page_text": "距离限制重置还剩 1小时 13分钟。等待或升级至 SuperGrok。"}
    )


def test_grok_free_tier_walls_are_rate_limited_across_locales():
    """Grok 免费层限额文案会随界面语言变化，两种语言都必须识别为额度墙。

    2026-09-29 实测：账号界面切到中文后，页面显示「免费版限额已达上限」，
    适配器因只有英文关键词而空等到 180s 超时，并跳过挂起机制连续重试。
    """
    # 英文原文（2026-09-28 §2.2 页面证据）
    assert page_is_rate_limited(
        {"page_text": "Free tier limit reached\nUpgrade to SuperGrok"}
    )
    # 中文原文（2026-09-29 05:18–05:32 页面证据，逐字复刻）
    grok_zh_wall = (
        "Best gua sha gift set for self-care\n"
        "免费版限额已达上限\n"
        "请稍后再试，或升级至 SuperGrok 享受更高限额和高级功能。\n"
        "升级到 SuperGrok"
    )
    assert page_is_rate_limited({"page_text": grok_zh_wall})


def test_grok_limit_diagnostic_preserves_visible_reset_hint():
    assert browser_skill.rate_limit_diagnostic(
        {"page_text": "距离限制重置还剩 1小时 13分钟。等待或升级至 SuperGrok。"}
    ) == "platform quota or rate limit detected: 距离限制重置还剩 1小时 13分钟"


def test_yuanbao_selects_full_answer_not_nested_source_url():
    full_answer = "完整回答：" + ("供应商、规格、来源与采购核验建议。" * 20)

    assert select_main_answer(
        "yuanbao", [full_answer, "https://example.com/source"]
    ) == full_answer


def test_text_url_extraction_drops_markdown_list_separator_after_url():
    text = "来源：https://example.com/product/123-abc-\n1\n下一项"

    assert extract_text_urls(text) == ["https://example.com/product/123-abc"]


def test_text_url_extraction_stops_at_invisible_source_label_separator():
    text = "链接：https://example.com/product?id=42\u2060ExampleSource"

    assert extract_text_urls(text) == ["https://example.com/product?id=42"]


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


def test_baidu_uses_current_official_entry_and_stable_dom_boundaries():
    config = PLATFORMS["baidu"]

    assert config["url"] == "https://chat.baidu.com/"
    assert config["composer_selector"] == "textarea.ci-textarea"
    assert config["answer_selector"] == ".chat-search-answer-generate"
    assert config["conversation_marker"] == "/search/"


def test_textbox_lookup_accepts_current_gemini_label():
    page = '@e44 textbox "Enter a prompt for Gemini" [empty]'
    assert BskCliClient._find_textbox_ref(
        page, PLATFORMS["gemini"]["textbox"]
    ) == "@e44"


def test_textbox_lookup_accepts_current_logged_in_doubao_label():
    page = '@e63 textbox "发消息或按住空格说话..." [empty]'
    assert BskCliClient._find_textbox_ref(
        page, PLATFORMS["doubao"]["textbox"]
    ) == "@e63"


def test_textbox_lookup_accepts_current_grok_label():
    page = '@e22 textbox "Ask Grok anything" [empty]'
    assert BskCliClient._find_textbox_ref(
        page, PLATFORMS["grok"]["textbox"]
    ) == "@e22"


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


class GeminiFilledDespiteErrorClient(FillFallbackClient):
    async def _composer_text(self, session_id, selector):
        return "hello"


class GeminiSilentFillClient(FillFallbackClient):
    async def _run_json(self, *args, timeout=30.0):
        self.calls.append(args)
        return {"value": True}

    async def _composer_text(self, session_id, selector):
        return ""


class GrokRerenderClient(BskCliClient):
    def __init__(self):
        super().__init__("browser", poll_interval=0)
        self.calls = []
        self.value = ""

    async def _run_json(self, *args, timeout=30.0):
        self.calls.append(args)
        if args[0] == "fill" and args[1] == "@stale":
            raise RuntimeError("fill target changed or lost focus before typing")
        if args[0] == "observe":
            return {"text": '@e22 textbox "Ask Grok anything" [empty]'}
        if args[0] == "fill" and args[1] == "@e22":
            self.value = args[3]
            return {"value": True}
        return {"value": True}

    async def _composer_text(self, session_id, selector):
        return self.value


class GrokExecFallbackClient(GrokRerenderClient):
    async def _run_json(self, *args, timeout=30.0):
        self.calls.append(args)
        if args[0] == "fill":
            raise RuntimeError("fill target changed or lost focus before typing")
        if args[0] == "evaluate":
            self.value = "hello grok"
            return {"value": self.value}
        return {"text": '@e22 textbox "Ask Grok anything" [empty]'}


class GrokSelectorFallbackClient(GrokRerenderClient):
    async def _run_json(self, *args, timeout=30.0):
        self.calls.append(args)
        if args[0] == "fill" and str(args[1]).startswith("@"):
            raise RuntimeError("fill target changed or lost focus before typing")
        if args[0] == "fill" and args[1] == "--selector":
            self.value = args[4]
            return {"value": True}
        return {"text": '@e22 textbox "Ask Grok anything" [empty]'}


class GrokNativeKeysClient(GrokRerenderClient):
    async def _run_json(self, *args, timeout=30.0):
        self.calls.append(args)
        if args[0] == "fill":
            raise RuntimeError("fill target changed or lost focus before typing")
        if args[0] == "press":
            key = args[1]
            if key == "Backspace":
                self.value = ""
            elif key not in {"Meta+A"}:
                self.value += " " if key == "Space" else key
            return {"value": True}
        if args[0] == "evaluate":
            raise AssertionError("DOM insertion should not run after native keys succeed")
        return {"text": '@e22 textbox "Ask Grok anything" [empty]'}


@pytest.mark.asyncio
async def test_grok_reobserves_and_verifies_prompt_after_target_loss():
    client = GrokRerenderClient()

    await client._enter_prompt("session", "grok", "@stale", "hello grok")

    assert client.value == "hello grok"
    assert any(call[0] == "observe" for call in client.calls)


@pytest.mark.asyncio
async def test_grok_uses_dom_insertion_when_fill_target_keeps_rerendering():
    client = GrokExecFallbackClient()

    await client._enter_prompt("session", "grok", "@stale", "hello grok")

    assert client.value == "hello grok"
    assert any(call[0] == "evaluate" for call in client.calls)


@pytest.mark.asyncio
async def test_grok_retries_fill_with_stable_selector_before_dom_insertion():
    client = GrokSelectorFallbackClient()

    await client._enter_prompt("session", "grok", "@stale", "hello grok")

    assert client.value == "hello grok"
    assert any(call[:2] == ("fill", "--selector") for call in client.calls)
    assert not any(call[0] == "evaluate" for call in client.calls)


@pytest.mark.asyncio
async def test_grok_uses_native_key_events_when_all_fill_targets_rerender():
    client = GrokNativeKeysClient()

    await client._enter_prompt("session", "grok", "@stale", "Hi 你")

    assert client.value == "Hi 你"
    assert any(call[:2] == ("press", "Space") for call in client.calls)


@pytest.mark.asyncio
async def test_grok_submits_through_stable_selector_not_stale_ref():
    client = FillFallbackClient()

    await client._submit_prompt("session", "grok", "@stale")

    assert client.calls[-1][0:2] == ("press", "Enter")
    assert "--selector" in client.calls[-1]
    assert "@stale" not in client.calls[-1]


class ComposerStateClient(BskCliClient):
    def __init__(self, values):
        super().__init__("browser", poll_interval=0)
        self.values = iter(values)
        self.calls = []

    async def _run_json(self, *args, timeout=30.0):
        self.calls.append(args)
        return {"value": next(self.values)}


@pytest.mark.asyncio
async def test_perplexity_standard_search_mode_must_be_confirmed():
    client = ComposerStateClient(
        [
            {"found": True, "active": False},
            {"found": True, "active": True},
        ]
    )

    assert await client._ensure_perplexity_standard_search("session") is True
    assert len(client.calls) == 2


class GeminiSubmitClient(BskCliClient):
    def __init__(self, states):
        super().__init__("browser", poll_interval=0)
        self.states = iter(states)
        self.clicks = 0

    async def _run_json(self, *args, timeout=30.0):
        if args[0] == "click":
            self.clicks += 1
            return {"value": True}
        if args[0] == "evaluate":
            return {"value": next(self.states)}
        return {"value": True}


class GeminiObservedSubmitClient(BskCliClient):
    def __init__(self):
        super().__init__("browser", poll_interval=0)
        self.calls = []

    async def _run_json(self, *args, timeout=30.0):
        self.calls.append(args)
        if args[0] == "observe":
            return {"text": '@e52 button "Send message"'}
        if args[0] == "evaluate":
            return {"value": {"submitted": True}}
        return {"value": True}


class GeminiEnterSubmitClient(BskCliClient):
    def __init__(self):
        super().__init__("browser", poll_interval=0)
        self.calls = []

    async def _run_json(self, *args, timeout=30.0):
        self.calls.append(args)
        if args[0] == "observe":
            return {"text": '@e47 textbox "Enter a prompt for Gemini"'}
        if args[0] == "click":
            raise RuntimeError("send button not found")
        if args[0] == "evaluate":
            return {"value": {"submitted": True}}
        return {"value": True}


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

    async def _ensure_perplexity_standard_search(self, session_id):
        return True

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


class DoubaoPostSubmitLoginClient(DelayedAnswerClient):
    pass


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


@pytest.mark.asyncio
async def test_doubao_dismisses_download_promotion_before_observing():
    client = FillFallbackClient()

    dismissed = await client._dismiss_blocking_overlays("session", "doubao")

    assert dismissed is True
    assert client.calls[-1][0] == "evaluate"
    assert "下载豆包电脑版" in client.calls[-1][1]


@pytest.mark.asyncio
async def test_baidu_dismisses_task_mode_promotion_before_observing():
    client = FillFallbackClient()

    dismissed = await client._dismiss_blocking_overlays("session", "baidu")

    assert dismissed is True
    assert client.calls[-1][0] == "evaluate"
    assert "全新上线任务模式" in client.calls[-1][1]


@pytest.mark.asyncio
async def test_doubao_post_submit_login_dialog_is_not_reported_as_timeout():
    client = DoubaoPostSubmitLoginClient(
        [
            {"answers": [], "links": [], "page_text": ""},
            {
                "answers": [],
                "links": [],
                "page_text": "登录以解锁更多功能 使用豆包或飞书账号登录 扫码登录",
            },
        ]
    )

    output = await client.probe("session", "doubao", "question", timeout=1)

    assert output.login_required is True
    assert output.failure is None
    assert output.diagnostic == "login required after submission"


def test_doubao_answer_boundary_targets_message_content():
    assert PLATFORMS["doubao"]["answer_selector"] == '[data-testid="message_text_content"]'


@pytest.mark.asyncio
async def test_doubao_source_trigger_stays_inside_latest_answer():
    client = FillFallbackClient()

    await client._open_source_panel("session", "doubao")

    expression = client.calls[-1][1]
    assert "const allowDocumentFallback = false" in expression


@pytest.mark.asyncio
async def test_source_panel_reader_filters_hidden_and_unlabelled_panels():
    client = FillFallbackClient()

    await client._source_panel_page("session", "doubao")

    expression = client.calls[-1][1]
    assert "panel.getClientRects().length" in expression
    assert "panelLabels.some" in expression


def test_gemini_answer_boundary_excludes_prompt_and_navigation():
    assert PLATFORMS["gemini"]["composer_selector"] == (
        'div[aria-label="Enter a prompt for Gemini"], '
        'rich-textarea [contenteditable="true"][role="textbox"], '
        'div[contenteditable="true"][role="textbox"][aria-multiline="true"]'
    )
    assert PLATFORMS["gemini"]["answer_selector"] == "message-content .markdown"


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
async def test_gemini_accepts_verified_value_when_fill_reports_target_change():
    client = GeminiFilledDespiteErrorClient()

    await client._enter_prompt("session", "gemini", "@e47", "hello")

    assert [call[0] for call in client.calls] == ["fill"]


@pytest.mark.asyncio
async def test_gemini_native_insert_dispatches_input_event_after_fill_rerender():
    client = FillFallbackClient()

    await client._enter_prompt("session", "gemini", "@stale", "hello")

    insertion = next(
        call[1]
        for call in reversed(client.calls)
        if call[0] == "evaluate" and "insertText" in call[1]
    )
    assert "querySelectorAll" in insertion
    assert "target.focus()" in insertion
    assert "dispatchEvent(new InputEvent('input'" in insertion


@pytest.mark.asyncio
async def test_gemini_does_not_trust_unverified_successful_fill():
    client = GeminiSilentFillClient()

    await client._enter_prompt("session", "gemini", "@e47", "hello")

    assert any(call[0] == "evaluate" and "insertText" in call[1] for call in client.calls)


@pytest.mark.asyncio
async def test_chatgpt_submits_using_native_send_button():
    client = FillFallbackClient()
    await client._submit_prompt("session", "chatgpt", "@e42")
    assert client.calls[-1][:2] == (
        "click",
        'button[aria-label="发送"], button[aria-label="Send prompt"]',
    )


@pytest.mark.asyncio
async def test_gemini_submits_using_native_send_button():
    client = FillFallbackClient()
    await client._submit_prompt("session", "gemini", "@e47")
    click = next(call for call in client.calls if call[0] == "click")
    assert click[:2] == (
        "click",
        'button[aria-label="发送"], button[aria-label="发送消息"], '
        'button[aria-label*="发送" i], button[aria-label="Send prompt"], '
        'button[aria-label*="Send" i]',
    )


@pytest.mark.asyncio
async def test_gemini_successful_native_click_does_not_duplicate_submission():
    client = GeminiSubmitClient(
        [
            {"submitted": False},
            {"submitted": False},
            {"submitted": True},
        ]
    )

    await client._submit_prompt("session", "gemini", "@e47")

    assert client.clicks == 1


@pytest.mark.asyncio
async def test_gemini_submission_check_uses_visible_composer():
    client = GeminiEnterSubmitClient()

    await client._submit_prompt("session", "gemini", "@e47")

    expression = next(call[1] for call in client.calls if call[0] == "evaluate")
    assert "find(node => node.getClientRects().length)" in expression


@pytest.mark.asyncio
async def test_gemini_refreshes_send_button_ref_before_clicking():
    client = GeminiObservedSubmitClient()

    await client._submit_prompt("session", "gemini", "@e47")

    click = next(call for call in client.calls if call[0] == "click")
    assert click[:2] == ("click", "@e52")


@pytest.mark.asyncio
async def test_gemini_falls_back_to_enter_when_send_button_is_missing():
    client = GeminiEnterSubmitClient()

    await client._submit_prompt("session", "gemini", "@e47")

    press = next(call for call in client.calls if call[0] == "press")
    assert press[:2] == ("press", "Enter")
    assert "--selector" in press
    assert PLATFORMS["gemini"]["composer_selector"] in press


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
async def test_perplexity_collects_only_current_answer_citations(monkeypatch):
    configure_source_panel(monkeypatch, "perplexity")
    config = {**PLATFORMS["perplexity"], "answer_sources_only": True}
    monkeypatch.setitem(PLATFORMS, "perplexity", config)
    client = SourceCollectorClient(
        [{"found": True, "opened": True}],
        [[{"url": "https://stale.example/old", "title": "Old session"}]],
    )

    sources, status, diagnostic = await client._collect_visible_sources(
        "session",
        "perplexity",
        [{"url": "https://current.example/cited", "title": "Current citation"}],
    )

    assert [source.url for source in sources] == ["https://current.example/cited"]
    assert all(source.source_role == SourceRole.CITED for source in sources)
    assert client.open_calls == 0
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
@pytest.mark.parametrize("platform", ["doubao", "deepseek", "yuanbao", "baidu"])
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
@pytest.mark.parametrize("platform", ["doubao", "deepseek", "yuanbao", "baidu"])
async def test_domestic_platforms_report_none_exposed_when_trigger_is_absent(platform):
    client = SourceCollectorClient([{"found": False, "opened": False}])

    sources, status, diagnostic = await client._collect_visible_sources(
        "session", platform, []
    )

    assert sources == []
    assert status == SourceCaptureStatus.NONE_EXPOSED
    assert diagnostic is None


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["doubao", "deepseek", "yuanbao", "baidu"])
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
@pytest.mark.parametrize("platform", ["chatgpt", "gemini", "grok"])
async def test_international_platforms_collect_cited_and_surfaced_fixture_sources(platform):
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
@pytest.mark.parametrize("platform", ["chatgpt", "gemini", "perplexity", "grok"])
async def test_international_platforms_report_none_exposed_without_trigger(platform):
    client = SourceCollectorClient([{"found": False, "opened": False}])

    sources, status, diagnostic = await client._collect_visible_sources(
        "session", platform, []
    )

    assert sources == []
    assert status == SourceCaptureStatus.NONE_EXPOSED
    assert diagnostic is None


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["chatgpt", "gemini", "grok"])
async def test_international_platforms_keep_citations_when_panel_open_fails(platform):
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


def test_perplexity_links_stay_scoped_to_selected_longest_answer():
    page = {
        "answer_entries": [
            {
                "text": "Short follow-up?",
                "links": [{"url": "https://follow-up.example", "title": "Suggestion"}],
            },
            {
                "text": "This is the complete researched answer with suppliers and evidence.",
                "links": [{"url": "https://evidence.example", "title": "Evidence"}],
            },
        ]
    }

    assert answer_links_for_text(
        page, "This is the complete researched answer with suppliers and evidence."
    ) == [{"url": "https://evidence.example", "title": "Evidence"}]


@pytest.mark.asyncio
async def test_baidu_transient_controls_are_not_accepted_as_answers():
    client = DelayedAnswerClient(
        [
            {"answers": [], "links": []},
            {"answers": ["正在搜索网络\n跳过"], "links": []},
            {"answers": ["正在搜索网络\n跳过"], "links": []},
            {"answers": ["正在搜索网络\n跳过"], "links": []},
        ]
    )

    output = await client.probe("session", "baidu", "question", timeout=0.01)

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
async def test_baidu_human_verification_returns_user_action_required(challenge_text):
    client = DelayedAnswerClient(
        [
            {"answers": [], "links": [], "page_text": ""},
            {"answers": [], "links": [], "page_text": challenge_text},
        ]
    )

    output = await client.probe("session", "baidu", "question", timeout=1)

    assert output.login_required is True
    assert output.diagnostic == "human verification required"


@pytest.mark.asyncio
async def test_gemini_unusual_traffic_page_requires_user_action():
    client = DelayedAnswerClient(
        [
            {"answers": [], "links": [], "page_text": ""},
            {
                "answers": [],
                "links": [],
                "page_text": "Our systems have detected unusual traffic from your computer network.",
            },
        ]
    )

    output = await client.probe("session", "gemini", "question", timeout=1)

    assert output.login_required is True
    assert output.diagnostic == "human verification required"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refusal",
    [
        (
            "I'm having a hard time fulfilling your request. "
            "Can I help you with something else instead?"
        ),
        "我只是一个语言模型，理解不了这个问题，因此没法帮上忙。",
        "我只是一个语言模型，无法提供这方面的帮助。",
        "身为一个语言模型，我没办法提供这方面的帮助。",
        "我只是一个语言模型，不具备这方面的信息或能力，因此没法帮到你。",
    ],
)
async def test_gemini_generic_refusal_is_not_returned_as_a_successful_answer(refusal):
    client = DelayedAnswerClient(
        [
            {"answers": [], "links": [], "page_text": ""},
            {"answers": [refusal], "links": [], "page_text": refusal},
            {"answers": [refusal], "links": [], "page_text": refusal},
            {"answers": [refusal], "links": [], "page_text": refusal},
        ]
    )

    output = await client.probe("session", "gemini", "Which suppliers?", timeout=1)

    assert output.failure == FailureKind.EXTRACTION_FAILED
    assert output.diagnostic == "Gemini returned a refusal response"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transient_error",
    [
        "I seem to be encountering an error. Can I try something else for you?",
        "I encountered an error doing what you asked. Could you try again?",
    ],
)
async def test_gemini_transient_error_is_not_returned_as_success(transient_error):
    client = DelayedAnswerClient(
        [
            {"answers": [], "links": [], "page_text": ""},
            {"answers": [transient_error], "links": [], "page_text": transient_error},
            {"answers": [transient_error], "links": [], "page_text": transient_error},
            {"answers": [transient_error], "links": [], "page_text": transient_error},
        ]
    )

    output = await client.probe("session", "gemini", "Which suppliers?", timeout=1)

    assert output.failure == FailureKind.EXTRACTION_FAILED
    assert output.diagnostic == "Gemini returned a transient platform error"


@pytest.mark.asyncio
async def test_gemini_overlapping_stream_prefix_is_collapsed():
    duplicated = (
        "A Drug Master File contains confidential manufacturing information while"
        "A Drug Master File contains voluntary manufacturing information "
        "that the FDA reviews with a referenced application."
    )
    expected = (
        "A Drug Master File contains voluntary manufacturing information "
        "that the FDA reviews with a referenced application."
    )
    client = DelayedAnswerClient(
        [
            {"answers": [], "links": [], "page_text": ""},
            {"answers": [duplicated], "links": [], "page_text": duplicated},
            {"answers": [duplicated], "links": [], "page_text": duplicated},
            {"answers": [duplicated], "links": [], "page_text": duplicated},
        ]
    )

    output = await client.probe("session", "gemini", "What is a DMF?", timeout=1)

    assert output.answer == expected


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
async def test_chatgpt_search_heading_is_not_returned_as_answer():
    heading = "核查供应商资料"
    client = DelayedAnswerClient(
        [
            {"answers": [], "links": []},
            {"answers": [heading], "links": []},
            {"answers": [heading], "links": []},
            {"answers": [heading], "links": []},
        ]
    )

    output = await client.probe("session", "chatgpt", "Research suppliers", timeout=0.01)

    assert output.failure == FailureKind.EXTRACTION_FAILED
    assert output.answer == ""


@pytest.mark.parametrize(
    "preamble",
    [
        "我来帮你查一下目前做 GEO 有实际案例的服务商。先检索几组关键词。",
        "我将结合 GEO 服务的行业特性，整理一套可直接落地的评分标准。",
    ],
)
def test_doubao_search_preamble_is_incomplete(preamble):
    assert browser_skill.is_incomplete_preamble("doubao", preamble) is True


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


class DelayedGrokClient(DelayedAnswerClient):
    """Grok 通道：observe 返回 Grok 输入框；答案区返回给定的页面状态。"""

    async def _run_json(self, *args, timeout=30.0):
        if args[0] == "navigate":
            return {"final_url": "https://grok.com/"}
        if args[0] == "observe":
            return {"text": '@e18 textbox "Ask Grok anything" [empty]'}
        return {"value": True}


@pytest.mark.asyncio
async def test_grok_chinese_quota_wall_is_rate_limited_not_timeout():
    """Grok 中文界面下的免费层额度墙必须被判为 RATE_LIMITED，而不是空等到 timeout。

    页面原文来自 2026-09-29 05:18–05:32 的真实抓取（会话
    39fd8bfd-d0cc-466c-830d-05e8a73ebd2c / a02f466e-9c99-4c19-894f-cbd049acd8d7）。
    """
    client = DelayedGrokClient(
        [
            {"answers": [], "links": [], "page_text": ""},
            {
                "answers": [],
                "links": [],
                "page_text": (
                    "Best gua sha gift set for self-care\n"
                    "免费版限额已达上限\n"
                    "请稍后再试，或升级至 SuperGrok 享受更高限额和高级功能。\n"
                    "升级到 SuperGrok"
                ),
            },
        ]
    )

    output = await client.probe("session", "grok", "Best gua sha gift set for self-care", timeout=0.01)

    assert output.failure == FailureKind.RATE_LIMITED
    assert "quota" in (output.diagnostic or "")


class FakeBrowserClient:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
        self.started = []
        self.stopped = []
        self.last_prompt = None
        self.last_timeout = None

    async def start(self, platform):
        self.started.append(platform)
        return "session-1"

    async def probe(self, session_id, platform, prompt, timeout):
        self.last_prompt = prompt
        self.last_timeout = timeout
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
async def test_explicit_timeout_overrides_baidu_default(tmp_path: Path):
    client = FakeBrowserClient(BrowserProbeOutput(answer="完整回答"))
    adapter = BrowserSkillAdapter(client=client, artifact_root=tmp_path)

    await adapter.run("baidu", ProbeRequest(prompt="测试", options={"timeout": 12}))

    assert client.last_timeout == 12


@pytest.mark.asyncio
async def test_doubao_uses_longer_default_timeout_for_deep_search(tmp_path: Path):
    client = FakeBrowserClient(BrowserProbeOutput(answer="完整回答"))
    adapter = BrowserSkillAdapter(client=client, artifact_root=tmp_path)

    await adapter.run("doubao", ProbeRequest(prompt="测试"))

    assert client.last_timeout == 300


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

    attempt = await adapter.run("baidu", ProbeRequest(prompt="测试"))

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
async def test_browser_adapter_classifies_gemini_submission_failure(tmp_path: Path):
    client = FakeBrowserClient(
        error=RuntimeError("Gemini submission state did not change after 3 attempts")
    )
    adapter = BrowserSkillAdapter(client=client, artifact_root=tmp_path)

    attempt = await adapter.run("gemini", ProbeRequest(prompt="测试"))

    assert attempt.status == JobStatus.FAILED
    assert attempt.failure == FailureKind.SEND_FAILED
    assert attempt.diagnostic == "Gemini submission state did not change after 3 attempts"


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

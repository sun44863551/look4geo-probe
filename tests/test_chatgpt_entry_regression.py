import pytest
from urllib.parse import parse_qs, urlsplit
from look4geo_probe.adapters.browser_skill import BskCliClient, PLATFORMS


def test_chatgpt_selector_supports_observed_dil_answer_renderer():
    selector = PLATFORMS["chatgpt"]["answer_selector"]
    assert '[data-message-author-role="assistant"] [data-dil-widget-copy-target]' in selector


class EntryClient(BskCliClient):
    def __init__(self, text):
        super().__init__("81483588", poll_interval=0)
        self.text = text
        self.calls = []

    async def _composer_text(self, session_id, selector):
        return self.text

    async def _run_json(self, *args, **kwargs):
        self.calls.append(args)
        return {"value": {"submitted": True}}


@pytest.mark.asyncio
async def test_chatgpt_silent_prefill_failure_is_detected():
    client = EntryClient("")
    with pytest.raises(RuntimeError, match="ChatGPT prompt entry via .* could not be verified"):
        await client._enter_prompt("session", "chatgpt", "@stale", "日本語の質問")


@pytest.mark.asyncio
async def test_empty_composer_never_counts_as_submission():
    client = EntryClient("")
    with pytest.raises(RuntimeError, match="ChatGPT prompt entry could not be verified"):
        await client._submit_prompt("session", "chatgpt", "@stale")
    assert not client.calls


@pytest.mark.asyncio
async def test_chatgpt_prefill_preserves_unicode_and_does_not_submit():
    client = EntryClient("日本語の質問")
    await client._enter_prompt("session", "chatgpt", "@stale", "日本語の質問")
    assert len(client.calls) == 1
    action, url, *rest = client.calls[0]
    assert action == "navigate"
    assert urlsplit(url).hostname == "chatgpt.com"
    assert parse_qs(urlsplit(url).query) == {"q": ["日本語の質問"]}
    assert rest == ["--wait-until", "domcontentloaded", "--session", "session"]


@pytest.mark.asyncio
async def test_chatgpt_session_warms_the_site_on_dedicated_browser_without_forcing_focus():
    client = EntryClient("")
    async def command(*args, **kwargs):
        client.calls.append(args)
        return {"session_id": "dedicated-session"}
    client._run_json = command
    assert await client.start("chatgpt") == "dedicated-session"
    assert client.calls == [
        ("session", "start", "--browser", "81483588"),
        ("navigate", PLATFORMS["chatgpt"]["url"], "--session", "dedicated-session"),
    ]

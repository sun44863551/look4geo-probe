import pytest
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
async def test_chatgpt_silent_fill_failure_is_detected():
    client = EntryClient("")
    with pytest.raises(RuntimeError, match="ChatGPT prompt entry could not be verified"):
        await client._enter_prompt("session", "chatgpt", "@stale", "日本語の質問")


@pytest.mark.asyncio
async def test_empty_composer_never_counts_as_submission():
    client = EntryClient("")
    with pytest.raises(RuntimeError, match="ChatGPT prompt entry could not be verified"):
        await client._submit_prompt("session", "chatgpt", "@stale")
    assert not client.calls


@pytest.mark.asyncio
async def test_chatgpt_fill_uses_stable_selector_and_checks_content():
    client = EntryClient("日本語の質問")
    await client._enter_prompt("session", "chatgpt", "@stale", "日本語の質問")
    assert client.calls[0] == ("click", PLATFORMS["chatgpt"]["composer_selector"], "--session", "session")
    assert client.calls[1][0] == "evaluate"
    assert "insertText" in client.calls[1][1]


@pytest.mark.asyncio
async def test_chatgpt_session_requests_foreground_on_dedicated_browser():
    client = EntryClient("")
    async def command(*args, **kwargs):
        client.calls.append(args)
        return {"session_id": "dedicated-session"}
    client._run_json = command
    assert await client.start("chatgpt") == "dedicated-session"
    assert client.calls == [("session", "start", "--browser", "81483588")]

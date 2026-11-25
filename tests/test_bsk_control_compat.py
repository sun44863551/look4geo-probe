import asyncio
import json

import pytest

from look4geo_probe.adapters.browser_skill import BskCliClient, BrowserProbeOutput, BrowserSkillAdapter
from look4geo_probe.models import ProbeRequest


@pytest.mark.asyncio
@pytest.mark.parametrize("action,strict", [
    (("click", "button[aria-label=Send]"), False),
    (("click", "@e7"), False),
    (("click", "--capture", "screen", "--image-x", "1", "--image-y", "2"), True),
    (("press", "Enter", "--selector", "textarea"), False),
    (("press", "Enter"), True),
    (("evaluate", "location.href"), True),
])
async def test_only_strict_actions_send_top_level_observation(monkeypatch, action, strict):
    calls = []

    async def process(*args, **kwargs):
        calls.append(args)
        class Process:
            returncode = 0
            async def communicate(self):
                return b'{"ok":true}', b''
        return Process()

    client = BskCliClient("81483588")
    async def context(*args):
        return "control", "top-level-receipt"
    monkeypatch.setattr(client, "_cooperate_context", context)
    monkeypatch.setattr("asyncio.create_subprocess_exec", process)
    await client._run_json(*action, "--session", "task")
    assert len(calls) == 1
    assert calls[0][calls[0].index("--context")+1] == "control"
    assert ("--observation" in calls[0]) is strict
    if strict:
        assert calls[0][calls[0].index("--observation")+1] == "top-level-receipt"


@pytest.mark.asyncio
async def test_successful_attempt_preserves_reviewable_conversation_url(tmp_path):
    class Client:
        async def start(self, platform):
            return "session"
        async def probe(self, *args):
            return BrowserProbeOutput(answer="Completed answer.", conversation_url="https://chatgpt.com/c/test")
        async def stop(self, session_id):
            pass

    result = await BrowserSkillAdapter(client=Client(), artifact_root=tmp_path).run(
        "chatgpt", ProbeRequest(prompt="Question")
    )
    assert result.conversation_url == "https://chatgpt.com/c/test"


@pytest.mark.asyncio
async def test_gemini_platform_submission_error_is_not_retried(tmp_path):
    class Client:
        starts = 0
        stops = 0
        async def start(self, platform):
            self.starts += 1
            return "session"
        async def probe(self, *args):
            raise RuntimeError("Gemini platform error: Something went wrong (1)")
        async def stop(self, session_id):
            self.stops += 1

    client = Client()
    result = await BrowserSkillAdapter(client=client, artifact_root=tmp_path).run(
        "gemini", ProbeRequest(prompt="Question")
    )
    assert result.failure.value == "send_failed"
    assert "Something went wrong (1)" in result.diagnostic
    assert client.starts == client.stops == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reason,effect", [
    ("user_aborted", "none"),
    ("user_activity_revoked", "none"),
    ("cooperation_sync_required", "unknown"),
])
async def test_control_interruption_and_unknown_effect_never_replay(monkeypatch, reason, effect):
    calls = []
    async def process(*args, **kwargs):
        calls.append(args)
        class Process:
            returncode = 1
            async def communicate(self):
                return json.dumps({"code": "permission_denied",
                    "message": "The strict observation changed before this action",
                    "data": {"reason": reason, "effect_state": effect}}).encode(), b""
        return Process()
    client = BskCliClient("81483588")
    async def context(*args):
        return "context", "observation"
    async def no_sleep(*args):
        pass
    monkeypatch.setattr(client, "_cooperate_context", context)
    monkeypatch.setattr("asyncio.create_subprocess_exec", process)
    monkeypatch.setattr("asyncio.sleep", no_sleep)
    with pytest.raises(Exception, match="strict observation changed"):
        await client._run_json("press", "Enter", "--selector", "textarea", "--session", "task")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_browser_timeout_reaps_only_the_spawned_cli_process(monkeypatch):
    class Child:
        returncode = None
        killed = False
        def kill(self):
            self.killed = True
            self.returncode = -9
        async def communicate(self):
            if not self.killed:
                raise asyncio.TimeoutError
            return b"", b""
    child = Child()
    async def spawn(*args, **kwargs):
        return child
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    with pytest.raises(asyncio.TimeoutError):
        await BskCliClient("81483588")._run_json("observe", "--session", "task", timeout=0.1)
    assert child.killed


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [
    ("navigate", "https://chatgpt.com/"),
    ("press", "Enter", "--selector", "#prompt-textarea"),
])
async def test_chatgpt_retries_connection_closed_only_during_navigation(monkeypatch, command):
    calls = []

    async def spawn(*args, **kwargs):
        calls.append(args)
        class Child:
            returncode = 1 if len(calls) == 1 else 0
            async def communicate(self):
                if self.returncode:
                    return b'{"code":"cdp_failed","message":"Page.navigate rejected: net::ERR_CONNECTION_CLOSED"}', b""
                return b'{"tab_id":1}', b""
        return Child()

    async def context(*args):
        return "", ""

    async def no_sleep(*args):
        pass

    client = BskCliClient("81483588")
    monkeypatch.setattr(client, "_cooperate_context", context)
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    monkeypatch.setattr("asyncio.sleep", no_sleep)
    if command[0] == "navigate":
        assert await client._run_json(*command, "--session", "task") == {"tab_id": 1}
        assert len(calls) == 2
    else:
        with pytest.raises(RuntimeError, match="ERR_CONNECTION_CLOSED"):
            await client._run_json(*command, "--session", "task")
        assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["https://www.perplexity.ai/", "https://chatgpt.com/"])
@pytest.mark.parametrize("explicit,expected", [((), "domcontentloaded"), (("--wait-until", "load"), "load")])
async def test_navigation_does_not_wait_for_unrelated_resources_by_default(monkeypatch, url, explicit, expected):
    calls = []
    async def spawn(*args, **kwargs):
        calls.append(args)
        class Child:
            returncode = 0
            async def communicate(self):
                return b'{"tab_id":1}', b""
        return Child()
    async def context(*args):
        return "", ""
    client = BskCliClient("81483588")
    monkeypatch.setattr(client, "_cooperate_context", context)
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    await client._run_json("navigate", url, *explicit, "--session", "task")
    assert calls[0][calls[0].index("--wait-until") + 1] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("expects_more,expected", [(False, "captured"), (True, "failed")])
async def test_chatgpt_unavailable_preview_is_not_a_missing_source_when_direct_link_was_captured(expects_more, expected):
    page_reads = []

    class Client(BskCliClient):
        async def _run_json(self, *args, **kwargs):
            script = args[1]
            if "return {count:triggers.length" in script:
                return {"value": {"count": 1, "targets": [
                    {"href": "https://example.org/article?utm_source=chatgpt.com", "expects_more": expects_more}
                ]}}
            if "t.click()" in script:
                return {"value": True}
            page_reads.append(script)
            return {"value": []}

    sources, status, diagnostic = await Client("81483588", poll_interval=0)._collect_visible_sources(
        "session", "chatgpt", [{"url": "https://example.org/article", "title": "Article"}]
    )
    assert [s.url for s in sources] == ["https://example.org/article"]
    assert status == expected
    assert bool(diagnostic) is expects_more
    assert bool(page_reads) is expects_more


@pytest.mark.asyncio
@pytest.mark.parametrize("expected_count,status,close_denied", [(1, "captured", False), (2, "failed", False), (1, "captured", True)])
async def test_chatgpt_grouped_citation_resolves_source_button_through_owned_tabs(expected_count, status, close_denied):
    class Client(BskCliClient):
        def __init__(self):
            super().__init__("81483588", poll_interval=0)
            self.calls = []
            self.opened = False
            self.tab_polls = 0
            self._session_tabs["session"] = 1

        async def _run_json(self, *args, **kwargs):
            self.calls.append(args)
            action = args[0]
            if action == "tab":
                if args[1] == "close":
                    if close_denied:
                        raise RuntimeError("permission_denied: Actions require an owned task tab; borrow user tabs explicitly")
                    return {"ok": True}
                if args[1] == "select":
                    return {"ok": True}
                assert args[2:4] == ("--scope", "agent")
                tabs = [{"tab_id": 1, "url": "https://chatgpt.com/c/test"}]
                if self.opened:
                    self.tab_polls += 1
                    url = "https://www.rfc-editor.org/rfc/rfc9309.html?utm_source=chatgpt.com"
                    tabs.append({"tab_id": 2, "url": url})
                    if self.tab_polls > 1:
                        tabs.append({"tab_id": 3, "url": url})
                return {"tabs": tabs}
            if action in {"click", "press"}:
                if "source-card" in str(args):
                    self.opened = True
                return {"ok": True}
            if action == "hover":
                return {"ok": True}
            script = args[1]
            if "return {count:triggers.length" in script:
                return {"value": {"count": 1, "targets": [{"href": None, "expected_count": expected_count}]}}
            if "buttons.forEach" in script:
                return {"value": True}
            if "b.setAttribute('data-look4geo-source-card'" in script:
                if not getattr(self, "panel_pending", False):
                    self.panel_pending = True
                    return {"ok": True, "tab_id": 1}
                return {"value": {"links": [], "buttons": [{"title": "RFC 9309"}]}}
            if "return t?.getAttribute('aria-expanded')==='true'" in script:
                return {"value": True}
            return {"value": True}

    client = Client()
    sources, observed_status, diagnostic = await client._collect_visible_sources("session", "chatgpt", [])
    assert observed_status == status
    assert bool(diagnostic) == (status == "failed")
    assert [s.url for s in sources] == ["https://www.rfc-editor.org/rfc/rfc9309.html"]
    assert sources[0].source_role.value == "cited"
    actions = [call[0] for call in client.calls]
    assert actions.index("scroll-to") < actions.index("press")
    assert sum(call[:2] == ("press", "Enter") for call in client.calls) == 2
    assert [call[2] for call in client.calls if call[:2] == ("tab", "close")] == (["3"] if close_denied else ["3", "2"])
    if close_denied:
        assert ("tab", "select", "1", "--session", "session") in client.calls
    assert any(call[0] == "evaluate" and "t.click()" in call[1] for call in client.calls)


def test_bsk_tab_management_does_not_append_unsupported_pinned_tab_flag():
    client = BskCliClient("81483588")
    client._session_tabs["session"] = 1
    command = ("tab", "list", "--scope", "agent", "--session", "session")
    assert client._args_with_pinned_tab(command) == command


@pytest.mark.asyncio
async def test_bsk_closing_source_tab_uses_its_own_control_context(monkeypatch):
    target_tabs = []
    calls = []

    async def context(session_id, timeout, tab_id):
        target_tabs.append(tab_id)
        return "context", "target-observation"

    async def spawn(*args, **kwargs):
        calls.append(args)
        class Child:
            returncode = 0
            async def communicate(self):
                return b'{"ok":true}', b""
        return Child()

    client = BskCliClient("81483588")
    client._session_tabs["session"] = 1
    monkeypatch.setattr(client, "_cooperate_context", context)
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    await client._run_json("tab", "close", "2", "--session", "session")
    assert target_tabs == [2]
    assert calls[0][calls[0].index("--observation") + 1] == "target-observation"


@pytest.mark.asyncio
async def test_chatgpt_does_not_click_a_covered_citation_badge():
    class Client(BskCliClient):
        def __init__(self):
            super().__init__("81483588", poll_interval=0)
            self.actions = []

        async def _run_json(self, *args, **kwargs):
            self.actions.append(args[0])
            return {"value": False if "elementFromPoint" in args[1] else True}

    client = Client()
    cards, complete, _ = await client._collect_chatgpt_source_group("session", "", 0, 1)
    assert cards == []
    assert not complete
    assert "click" not in client.actions


@pytest.mark.asyncio
async def test_chatgpt_reopens_source_group_after_source_tab_dismisses_popover():
    class Client(BskCliClient):
        def __init__(self):
            super().__init__("81483588", poll_interval=0)
            self.expanded = False
            self.activations = []

        async def _chatgpt_source_target_ready(self, session_id, selector):
            return True

        async def _run_json(self, *args, **kwargs):
            if args[:2] == ("press", "Enter"):
                self.expanded = True
                return {"ok": True}
            if args[0] != "evaluate":
                return {"ok": True}
            script = args[1]
            if "buttons.forEach" in script:
                return {"value": self.expanded}
            if "return t?.getAttribute" in script:
                return {"value": self.expanded}
            if "b.setAttribute('data-look4geo-source-card'" in script:
                return {"value": {"links": [], "buttons": [
                    {"title": "Source one"}, {"title": "Source two"},
                ]}}
            return {"value": True}

        async def _chatgpt_source_button_url(self, session_id, card_id):
            assert self.expanded
            self.activations.append(card_id)
            self.expanded = False
            return f"https://example.org/{card_id}", True

    client = Client()
    cards, complete, interrupted = await client._collect_chatgpt_source_group(
        "session", "const triggers=[]; ", 0, 2
    )
    assert client.activations == ["0-0", "0-1"]
    assert len(cards) == 2
    assert complete and not interrupted

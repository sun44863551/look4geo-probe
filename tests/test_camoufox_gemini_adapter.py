import os
from pathlib import Path

import pytest

from look4geo_probe.adapters.camoufox_gemini import (
    CamoufoxGeminiAdapter,
    CamoufoxRuntime,
    GeminiBrowserResult,
    GeminiPageDriver,
    extract_gemini_answer,
    extract_gemini_sources,
)
from look4geo_probe.adapters.types import AdapterHealth
from look4geo_probe.models import (
    FailureKind,
    JobStatus,
    ProbeRequest,
    SourceCaptureStatus,
    SourceEvidenceOrigin,
    SourceRole,
)


class FakeRuntime:
    def __init__(self, health=AdapterHealth(True, "ready")):
        self.health_result = health
        self.health_profiles: list[Path] = []
        self.login_profiles: list[Path] = []
        self.probe_timeouts: list[float] = []

    async def health(self, profile_dir: Path) -> AdapterHealth:
        self.health_profiles.append(profile_dir)
        return self.health_result

    async def login(self, profile_dir: Path) -> dict:
        self.login_profiles.append(profile_dir)
        return {"status": "succeeded", "platform": "gemini"}

    async def probe(
        self, profile_dir: Path, prompt: str, timeout: float, artifact_dir: Path
    ) -> GeminiBrowserResult:
        self.probe_timeouts.append(timeout)
        return GeminiBrowserResult(status=JobStatus.SUCCEEDED, answer="answer")


def make_adapter(tmp_path: Path, runtime=None) -> CamoufoxGeminiAdapter:
    return CamoufoxGeminiAdapter(
        runtime or FakeRuntime(),
        tmp_path / "profile",
        tmp_path / "artifacts",
    )


def test_constructor_does_not_create_profile_or_artifact_directories(tmp_path):
    adapter = make_adapter(tmp_path)

    assert adapter.name == "camoufox_gemini"
    assert not (tmp_path / "profile").exists()
    assert not (tmp_path / "artifacts").exists()


@pytest.mark.asyncio
async def test_adapter_uses_300_second_default_for_long_gemini_answers(tmp_path):
    runtime = FakeRuntime()

    await make_adapter(tmp_path, runtime).run("gemini", ProbeRequest(prompt="question"))

    assert runtime.probe_timeouts == [300.0]


@pytest.mark.asyncio
async def test_health_delegates_for_gemini(tmp_path):
    runtime = FakeRuntime(AdapterHealth(True, "browser installed"))
    adapter = make_adapter(tmp_path, runtime)

    result = await adapter.health("gemini")

    assert result == AdapterHealth(True, "browser installed")
    assert runtime.health_profiles == [tmp_path / "profile"]


@pytest.mark.asyncio
async def test_adapter_rejects_non_gemini_platforms(tmp_path):
    adapter = make_adapter(tmp_path)

    with pytest.raises(ValueError, match="only supports gemini"):
        await adapter.health("chatgpt")
    with pytest.raises(ValueError, match="only supports gemini"):
        await adapter.login("chatgpt")
    with pytest.raises(ValueError, match="only supports gemini"):
        await adapter.run("chatgpt", ProbeRequest(prompt="hello"))


@pytest.mark.asyncio
async def test_health_rejects_profile_path_that_is_a_file(tmp_path):
    profile = tmp_path / "profile"
    profile.write_text("not a directory", encoding="utf-8")
    adapter = CamoufoxGeminiAdapter(FakeRuntime(), profile, tmp_path / "artifacts")

    result = await adapter.health("gemini")

    assert result.ok is False
    assert result.detail == "profile path is not a directory"


@pytest.mark.asyncio
async def test_health_rejects_profile_parent_without_write_bits(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    adapter = CamoufoxGeminiAdapter(
        FakeRuntime(), locked / "profile", tmp_path / "artifacts"
    )

    try:
        result = await adapter.health("gemini")
    finally:
        locked.chmod(0o755)

    assert result.ok is False
    assert result.detail == "profile parent is not writable"


@pytest.mark.asyncio
async def test_login_delegates_without_exposing_credentials(tmp_path):
    runtime = FakeRuntime()
    adapter = make_adapter(tmp_path, runtime)

    result = await adapter.login("gemini")

    assert result == {"status": "succeeded", "platform": "gemini"}
    assert runtime.login_profiles == [tmp_path / "profile"]


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    @property
    def last(self):
        return self

    async def count(self):
        if "message-content" in self.selector:
            return 1 if self.page.answers else 0
        if "contenteditable" in self.selector or "prompt for Gemini" in self.selector:
            self.page.composer_checks += 1
            if (
                self.page.composer_after_checks is not None
                and self.page.composer_checks >= self.page.composer_after_checks
            ):
                self.page.has_composer = True
            return 1 if self.page.has_composer else 0
        if "Send" in self.selector or "发送" in self.selector:
            return 1
        if "Stop" in self.selector or "停止" in self.selector:
            if self.page.transition == "stop" and self.page.sent and not self.page.stop_seen:
                self.page.stop_seen = True
                return 1
            return 0
        if "conversation-turn" in self.selector:
            return self.page.turns
        return 1

    async def fill(self, value):
        self.page.fill_calls += 1
        if self.page.detach_first_fill and self.page.fill_calls == 1:
            raise RuntimeError("element detached")
        self.page.composer = value

    async def inner_text(self):
        if self.selector == "body":
            return self.page.body
        if "message-content" in self.selector:
            if self.page.answer_reads:
                return self.page.answer_reads.pop(0)
            return self.page.answers[-1]
        return self.page.composer

    async def click(self, **kwargs):
        self.page.click_calls += 1
        if self.page.send_click_fails:
            raise TimeoutError("send button unavailable")
        if self.page.click_calls <= self.page.silent_clear_attempts:
            self.page.composer = ""
            return
        if self.page.click_calls < self.page.transition_at:
            return
        self.page.sent = True
        if self.page.responses:
            response = self.page.responses.pop(0)
            self.page.answers = [response]
            self.page.answer_reads = [response, response]
        if self.page.transition == "composer":
            self.page.composer = ""
        elif self.page.transition == "url":
            self.page.composer = ""
            self.page.url = "https://gemini.google.com/app/conversation"
        elif self.page.transition == "turn":
            self.page.turns += 1

    async def press(self, key):
        self.page.press_calls += 1
        if key == "Enter" and self.page.enter_sends:
            self.page.sent = True
            self.page.composer = ""
            self.page.url = "https://gemini.google.com/app/conversation"


class FakePage:
    def __init__(
        self,
        *,
        body="",
        has_composer=True,
        transition="url",
        transition_at=1,
        answers=("answer", "answer"),
        detach_first_fill=False,
        navigation_timeout=False,
        composer_after_checks=None,
        send_click_fails=False,
        enter_sends=False,
        silent_clear_attempts=0,
        responses=(),
    ):
        self.body = body
        self.has_composer = has_composer
        self.transition = transition
        self.transition_at = transition_at
        self.answers = list(answers)
        self.answer_reads = list(answers)
        self.detach_first_fill = detach_first_fill
        self.navigation_timeout = navigation_timeout
        self.composer_after_checks = composer_after_checks
        self.composer_checks = 0
        self.send_click_fails = send_click_fails
        self.enter_sends = enter_sends
        self.silent_clear_attempts = silent_clear_attempts
        self.responses = list(responses)
        self.composer = ""
        self.fill_calls = 0
        self.click_calls = 0
        self.press_calls = 0
        self.locator_calls: list[str] = []
        self.sent = False
        self.stop_seen = False
        self.turns = 0
        self.url = "https://gemini.google.com/app"

    async def goto(self, url, **kwargs):
        if self.navigation_timeout:
            raise TimeoutError("navigation timed out")
        self.url = url

    def locator(self, selector):
        self.locator_calls.append(selector)
        return FakeLocator(self, selector)


@pytest.mark.asyncio
async def test_page_driver_login_succeeds_only_when_composer_is_visible(tmp_path):
    success = await GeminiPageDriver(FakePage()).login()
    blocked = await GeminiPageDriver(
        FakePage(has_composer=False), poll_interval=0.001, login_timeout=0.01
    ).login()

    assert success["status"] == "succeeded"
    assert blocked["status"] == "user_action_required"


@pytest.mark.asyncio
async def test_page_driver_login_waits_for_user_to_complete_visible_login():
    page = FakePage(has_composer=False, composer_after_checks=3)

    result = await GeminiPageDriver(
        page, poll_interval=0.001, login_timeout=0.1
    ).login()

    assert result["status"] == "succeeded"
    assert page.composer_checks >= 3


@pytest.mark.asyncio
async def test_page_driver_login_rejects_anonymous_gemini_composer():
    page = FakePage(body="Sign in to save activity", has_composer=True)

    result = await GeminiPageDriver(
        page, poll_interval=0.001, login_timeout=0.01
    ).login()

    assert result["status"] == "user_action_required"


@pytest.mark.asyncio
async def test_page_driver_re_resolves_detached_composer_and_verifies_prompt(tmp_path):
    page = FakePage(detach_first_fill=True)

    result = await GeminiPageDriver(page).probe("exact prompt", 1, tmp_path)

    assert result.status == JobStatus.SUCCEEDED
    assert result.answer == "answer"
    assert page.fill_calls == 2
    assert page.composer == ""


@pytest.mark.asyncio
async def test_page_driver_retries_send_three_times(tmp_path):
    page = FakePage(transition_at=3)

    result = await GeminiPageDriver(
        page, poll_interval=0.001, send_transition_timeout=0.01
    ).probe("hello", 1, tmp_path)

    assert result.status == JobStatus.SUCCEEDED
    assert page.click_calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ["stop", "url", "turn"])
async def test_page_driver_accepts_verified_send_transitions(tmp_path, transition):
    result = await GeminiPageDriver(FakePage(transition=transition)).probe(
        "hello", 1, tmp_path
    )

    assert result.status == JobStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_page_driver_refills_after_silent_composer_clear(tmp_path):
    page = FakePage(transition="url", silent_clear_attempts=1)

    result = await GeminiPageDriver(
        page, poll_interval=0.001, send_transition_timeout=0.01
    ).probe("hello", 1, tmp_path)

    assert result.status == JobStatus.SUCCEEDED
    assert page.fill_calls == 2
    assert page.click_calls == 2


@pytest.mark.asyncio
async def test_page_driver_rejects_composer_clear_without_submission(tmp_path):
    page = FakePage(transition="url", silent_clear_attempts=3)

    result = await GeminiPageDriver(
        page, poll_interval=0.001, send_transition_timeout=0.01
    ).probe("hello", 1, tmp_path)

    assert result.status == JobStatus.FAILED
    assert result.failure == FailureKind.SEND_FAILED
    assert page.fill_calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transient",
    [
        "I seem to be encountering an error. Can I try something else for you?",
        "I encountered an error doing what you asked. Could you try again?",
    ],
)
async def test_page_driver_retries_transient_gemini_error_answer(tmp_path, transient):
    page = FakePage(
        responses=(
            transient,
            "Python 3.12.0 was released on October 2, 2023.",
        )
    )

    result = await GeminiPageDriver(page, poll_interval=0.001).probe(
        "When was Python 3.12.0 released?", 1, tmp_path
    )

    assert result.status == JobStatus.SUCCEEDED
    assert result.answer == "Python 3.12.0 was released on October 2, 2023."
    assert page.click_calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "failure"),
    [
        ("Sign in to continue to Gemini", FailureKind.LOGIN_REQUIRED),
        ("Sign in to save activity", FailureKind.LOGIN_REQUIRED),
        ("Before you continue to Google", FailureKind.LOGIN_REQUIRED),
        ("Complete this CAPTCHA", FailureKind.LOGIN_REQUIRED),
        ("Our systems have detected unusual traffic", FailureKind.LOGIN_REQUIRED),
        ("You've reached your limit. Try again later", FailureKind.RATE_LIMITED),
    ],
)
async def test_page_driver_classifies_blocking_pages(tmp_path, body, failure):
    result = await GeminiPageDriver(FakePage(body=body)).probe("hello", 1, tmp_path)

    assert result.status in {JobStatus.FAILED, JobStatus.WAITING_FOR_LOGIN}
    assert result.failure == failure


@pytest.mark.asyncio
async def test_page_driver_classifies_navigation_timeout(tmp_path):
    result = await GeminiPageDriver(FakePage(navigation_timeout=True)).probe(
        "hello", 1, tmp_path
    )

    assert result.status == JobStatus.FAILED
    assert result.failure == FailureKind.TIMEOUT


@pytest.mark.asyncio
async def test_page_driver_fails_after_three_unchanged_send_attempts(tmp_path):
    page = FakePage(transition_at=99)

    result = await GeminiPageDriver(
        page, poll_interval=0.001, send_transition_timeout=0.01
    ).probe("hello", 1, tmp_path)

    assert result.status == JobStatus.FAILED
    assert result.failure == FailureKind.SEND_FAILED
    assert page.click_calls == 3


@pytest.mark.asyncio
async def test_page_driver_waits_for_authenticated_composer_to_load(tmp_path):
    page = FakePage(has_composer=False, composer_after_checks=3)

    result = await GeminiPageDriver(page, poll_interval=0.001).probe(
        "hello", 1, tmp_path
    )

    assert result.status == JobStatus.SUCCEEDED
    assert page.composer_checks >= 3


@pytest.mark.asyncio
async def test_page_driver_falls_back_to_enter_when_send_button_is_unavailable(
    tmp_path,
):
    page = FakePage(send_click_fails=True, enter_sends=True)

    result = await GeminiPageDriver(page).probe("hello", 1, tmp_path)

    assert result.status == JobStatus.SUCCEEDED
    assert page.press_calls == 1


def test_camoufox_runtime_defaults_to_headed_macos_and_pinned_browser(tmp_path):
    runtime = CamoufoxRuntime(
        tmp_path, headless=False, browser="152.0.4-beta.30"
    )

    assert runtime.root == tmp_path
    assert runtime.headless is False
    assert runtime.browser == "152.0.4-beta.30"


def test_camoufox_runtime_scopes_and_restores_content_sandbox_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOZ_DISABLE_CONTENT_SANDBOX", "original")
    runtime = CamoufoxRuntime(
        tmp_path,
        headless=False,
        browser="152.0.4-beta.30",
        disable_content_sandbox=True,
    )

    with runtime._local_environment():
        assert os.environ["XDG_CACHE_HOME"] == str(runtime.cache_dir)
        assert os.environ["MOZ_DISABLE_CONTENT_SANDBOX"] == "1"

    assert os.environ["MOZ_DISABLE_CONTENT_SANDBOX"] == "original"


class ExtractionLocator:
    def __init__(self, texts=(), links=(), click=None):
        self.texts = list(texts)
        self.links = list(links)
        self._click = click

    @property
    def last(self):
        return ExtractionLocator(self.texts[-1:], self.links)

    async def count(self):
        return len(self.texts) or len(self.links)

    async def inner_text(self):
        return self.texts[-1] if self.texts else ""

    async def evaluate_all(self, script):
        return self.links

    def nth(self, index):
        return ExtractionLocator(click=lambda: self._click(index))

    async def click(self, **kwargs):
        if self._click:
            self._click()


class ExtractionKeyboard:
    def __init__(self, page):
        self.page = page

    async def press(self, key):
        if key == "Escape":
            self.page.open_citation = None


class ExtractionPage:
    def __init__(self, answers, cited=(), surfaced=(), citation_dialogs=()):
        self.answers = answers
        self.cited = cited
        self.surfaced = surfaced
        self.citation_dialogs = list(citation_dialogs)
        self.open_citation = None
        self.keyboard = ExtractionKeyboard(self)

    def locator(self, selector):
        if selector == "message-content .markdown":
            return ExtractionLocator(self.answers)
        if "message-content .markdown a" in selector:
            return ExtractionLocator(links=self.cited)
        if "View source details for citation" in selector:
            return ExtractionLocator(
                links=[{} for _ in self.citation_dialogs],
                click=lambda index: setattr(self, "open_citation", index),
            )
        if '[role="dialog"]' in selector:
            links = (
                self.citation_dialogs[self.open_citation]
                if self.open_citation is not None
                else ()
            )
            return ExtractionLocator(links=links)
        if "source-panel" in selector:
            return ExtractionLocator(links=self.surfaced)
        return ExtractionLocator()


@pytest.mark.asyncio
async def test_extracts_only_newest_gemini_answer():
    answer = await extract_gemini_answer(ExtractionPage(["old answer", "new answer"]))

    assert answer == "new answer"


@pytest.mark.asyncio
async def test_extracts_normalizes_and_merges_gemini_sources():
    page = ExtractionPage(
        ["answer"],
        cited=[
            {
                "url": "https://www.google.com/url?url=https%3A%2F%2Fevidence.example%2Fdoc%3Futm_source%3Dgemini",
                "title": "Evidence",
            }
        ],
        surfaced=[
            {"url": "https://evidence.example/doc", "title": "Duplicate"},
            {"url": "https://other.example/source?utm_medium=ai", "title": "Other"},
            {"url": "https://gemini.google.com/app", "title": "Gemini UI"},
        ],
    )

    sources = await extract_gemini_sources(page)

    assert [source.url for source in sources] == [
        "https://evidence.example/doc",
        "https://other.example/source",
    ]
    assert sources[0].source_role == SourceRole.CITED
    assert sources[0].evidence_origin == SourceEvidenceOrigin.ANSWER_DOM
    assert sources[0].linked_in_answer is True
    assert sources[1].source_role == SourceRole.SURFACED
    assert sources[1].evidence_origin == SourceEvidenceOrigin.SOURCE_PANEL


@pytest.mark.asyncio
async def test_extracts_links_hidden_behind_gemini_citation_buttons():
    page = ExtractionPage(
        ["answer"],
        citation_dialogs=[
            [
                {
                    "url": "https://support.apple.com/en-sg/121555",
                    "title": "Mac mini (2024) - Tech Specs",
                }
            ],
            [
                {
                    "url": "https://www.racksolutions.com/mac-mini-m4?utm_source=gemini",
                    "title": "Mac mini M4 compatibility guide",
                }
            ],
        ],
    )

    sources = await extract_gemini_sources(page)

    assert [source.url for source in sources] == [
        "https://support.apple.com/en-sg/121555",
        "https://www.racksolutions.com/mac-mini-m4",
    ]
    assert all(source.source_role == SourceRole.CITED for source in sources)
    assert all(
        source.evidence_origin == SourceEvidenceOrigin.SOURCE_PANEL
        for source in sources
    )


@pytest.mark.asyncio
async def test_adapter_converts_browser_answer_and_sources(tmp_path):
    runtime = FakeRuntime()
    runtime.probe = lambda *args: None

    async def probe(profile_dir, prompt, timeout, artifact_dir):
        return GeminiBrowserResult(
            status=JobStatus.SUCCEEDED,
            answer="A valid Gemini answer with useful evidence.",
            cited_links=(("https://evidence.example/doc?utm_source=ai", "Evidence"),),
            surfaced_links=(("https://other.example/source", "Other"),),
            artifact_paths=(str(tmp_path / "answer.png"),),
        )

    runtime.probe = probe
    attempt = await make_adapter(tmp_path, runtime).run(
        "gemini", ProbeRequest(prompt="question")
    )

    assert attempt.status == JobStatus.SUCCEEDED
    assert attempt.raw_answer.startswith("A valid Gemini answer")
    assert attempt.source_capture_status == SourceCaptureStatus.CAPTURED
    assert [citation.url for citation in attempt.citations] == [
        "https://evidence.example/doc"
    ]
    assert [source.url for source in attempt.sources] == [
        "https://evidence.example/doc",
        "https://other.example/source",
    ]
    assert attempt.query_original == attempt.query_sent == "question"
    assert attempt.query_normalized is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    ["", "Ask away, sun!", "question", "Our systems have detected unusual traffic"],
)
async def test_adapter_rejects_invalid_or_blocking_answers(tmp_path, answer):
    runtime = FakeRuntime()

    async def probe(profile_dir, prompt, timeout, artifact_dir):
        return GeminiBrowserResult(status=JobStatus.SUCCEEDED, answer=answer)

    runtime.probe = probe
    attempt = await make_adapter(tmp_path, runtime).run(
        "gemini", ProbeRequest(prompt="question")
    )

    assert attempt.status == JobStatus.FAILED
    assert attempt.failure == FailureKind.EXTRACTION_FAILED


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
async def test_adapter_rejects_gemini_generic_refusal(tmp_path, refusal):
    runtime = FakeRuntime()

    async def probe(profile_dir, prompt, timeout, artifact_dir):
        return GeminiBrowserResult(
            status=JobStatus.SUCCEEDED,
            answer=refusal,
        )

    runtime.probe = probe
    attempt = await make_adapter(tmp_path, runtime).run(
        "gemini", ProbeRequest(prompt="Which suppliers can provide this API?")
    )

    assert attempt.status == JobStatus.FAILED
    assert attempt.failure == FailureKind.EXTRACTION_FAILED
    assert attempt.diagnostic == "Gemini returned a refusal response"


@pytest.mark.asyncio
async def test_adapter_records_none_exposed_separately_from_answer_success(tmp_path):
    runtime = FakeRuntime()

    async def probe(profile_dir, prompt, timeout, artifact_dir):
        return GeminiBrowserResult(
            status=JobStatus.SUCCEEDED,
            answer="A valid answer without any visible source links.",
        )

    runtime.probe = probe
    attempt = await make_adapter(tmp_path, runtime).run(
        "gemini", ProbeRequest(prompt="question")
    )

    assert attempt.status == JobStatus.SUCCEEDED
    assert attempt.source_capture_status == SourceCaptureStatus.NONE_EXPOSED
    assert attempt.sources == []

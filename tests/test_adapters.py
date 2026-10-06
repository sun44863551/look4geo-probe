import asyncio

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from look4geo_probe.adapters.base import ProbeAdapter
from look4geo_probe.adapters.registry import AdapterRegistry, AdapterChain, NoHealthyAdapter
from look4geo_probe.adapters.types import AdapterHealth
from look4geo_probe.models import FailureKind, JobStatus, PlatformAttempt, ProbeRequest


class NamedAdapter(ProbeAdapter):
    def __init__(self, name: str):
        self.name = name


def test_registry_uses_browser_fallback_when_primary_is_unhealthy():
    registry = AdapterRegistry(
        {"ai_search_hub": NamedAdapter("ai_search_hub"), "browser_skill": NamedAdapter("browser_skill")},
        {"gemini": ["ai_search_hub", "browser_skill"]},
    )
    adapter = registry.resolve(
        "gemini", health={"ai_search_hub": False, "browser_skill": True}
    )
    assert adapter.name == "browser_skill"


def test_registry_rejects_platform_without_healthy_adapter():
    registry = AdapterRegistry(
        {"browser_skill": NamedAdapter("browser_skill")}, {"chatgpt": ["browser_skill"]}
    )
    with pytest.raises(NoHealthyAdapter):
        registry.resolve("chatgpt", health={"browser_skill": False})


class ResultAdapter(ProbeAdapter):
    def __init__(self, name, status, healthy=True, failure=None):
        self.name = name
        self.status = status
        self.healthy = healthy
        self.calls = 0
        self.failure = failure

    async def health(self, platform):
        return AdapterHealth(self.healthy, self.name)

    async def run(self, platform, request):
        self.calls += 1
        return PlatformAttempt(
            platform=platform,
            adapter=self.name,
            status=self.status,
            raw_answer="ok" if self.status == JobStatus.SUCCEEDED else "",
            diagnostic=None if self.status == JobStatus.SUCCEEDED else f"{self.name} failed",
            failure=self.failure,
        )

    async def login(self, platform):
        return {"adapter": self.name}


@pytest.mark.asyncio
async def test_gemini_login_opens_persistent_camoufox_profile_when_available():
    browser = ResultAdapter("browser_skill", JobStatus.SUCCEEDED)
    camoufox = ResultAdapter("camoufox_gemini", JobStatus.SUCCEEDED)

    result = await AdapterChain("gemini", [browser, camoufox]).login("gemini")

    assert result == {"adapter": "camoufox_gemini"}


@pytest.mark.asyncio
async def test_adapter_chain_falls_back_after_primary_failure():
    primary = ResultAdapter("browser_skill", JobStatus.FAILED)
    fallback = ResultAdapter("ai_search_hub", JobStatus.SUCCEEDED)
    chain = AdapterChain("gemini", [primary, fallback])

    result = await chain.run("gemini", ProbeRequest(prompt="hello"))

    assert result.status == JobStatus.SUCCEEDED
    assert result.adapter == "ai_search_hub"
    assert result.diagnostic == "fallback after browser_skill: browser_skill failed"
    assert primary.calls == fallback.calls == 1


@pytest.mark.asyncio
async def test_adapter_chain_skips_unhealthy_adapter():
    primary = ResultAdapter("browser_skill", JobStatus.SUCCEEDED, healthy=False)
    fallback = ResultAdapter("ai_search_hub", JobStatus.SUCCEEDED)
    chain = AdapterChain("gemini", [primary, fallback])

    result = await chain.run("gemini", ProbeRequest(prompt="hello"))

    assert result.adapter == "ai_search_hub"
    assert primary.calls == 0


@pytest.mark.asyncio
async def test_adapter_chain_preserves_final_timeout_failure_kind():
    primary = ResultAdapter(
        "browser_skill", JobStatus.FAILED, failure=FailureKind.TIMEOUT
    )
    fallback = ResultAdapter(
        "ai_search_hub", JobStatus.FAILED, failure=FailureKind.TIMEOUT
    )

    result = await AdapterChain("gemini", [primary, fallback]).run(
        "gemini", ProbeRequest(prompt="hello")
    )

    assert result.status == JobStatus.FAILED
    assert result.failure == FailureKind.TIMEOUT


@pytest.mark.asyncio
async def test_adapter_chain_stops_on_login_required_instead_of_waiting_for_fallback():
    primary = ResultAdapter(
        "browser_skill",
        JobStatus.WAITING_FOR_LOGIN,
        failure=FailureKind.LOGIN_REQUIRED,
    )
    fallback = ResultAdapter("ai_search_hub", JobStatus.SUCCEEDED)

    result = await AdapterChain("doubao", [primary, fallback]).run(
        "doubao", ProbeRequest(prompt="hello")
    )

    assert result.status == JobStatus.WAITING_FOR_LOGIN
    assert result.failure == FailureKind.LOGIN_REQUIRED
    assert primary.calls == 1
    assert fallback.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [FailureKind.SEND_FAILED, FailureKind.EXTRACTION_FAILED, FailureKind.TIMEOUT],
)
async def test_gemini_chain_falls_back_after_non_terminal_camoufox_failure(failure):
    camoufox = ResultAdapter(
        "camoufox_gemini", JobStatus.FAILED, failure=failure
    )
    browser = ResultAdapter("browser_skill", JobStatus.SUCCEEDED)

    result = await AdapterChain("gemini", [camoufox, browser]).run(
        "gemini", ProbeRequest(prompt="hello")
    )

    assert result.status == JobStatus.SUCCEEDED
    assert result.adapter == "browser_skill"
    assert camoufox.calls == browser.calls == 1


class SlowAdapter(ResultAdapter):
    async def run(self, platform, request):
        self.calls += 1
        await asyncio.sleep(1)
        raise AssertionError("hard timeout did not stop slow adapter")


class PlaywrightTimeoutAdapter(ResultAdapter):
    async def run(self, platform, request):
        self.calls += 1
        raise PlaywrightTimeoutError("browser launch timed out")


@pytest.mark.asyncio
async def test_adapter_chain_playwright_timeout_falls_back_and_classifies_timeout():
    primary = PlaywrightTimeoutAdapter("camoufox_gemini", JobStatus.SUCCEEDED)
    fallback = ResultAdapter("browser_skill", JobStatus.SUCCEEDED)

    result = await AdapterChain("gemini", [primary, fallback]).run(
        "gemini", ProbeRequest(prompt="hello")
    )

    assert result.status == JobStatus.SUCCEEDED
    assert result.adapter == "browser_skill"
    assert "camoufox_gemini: browser timeout" in (result.diagnostic or "")
    assert primary.calls == fallback.calls == 1


@pytest.mark.asyncio
async def test_adapter_chain_hard_timeout_falls_back_and_classifies_timeout():
    primary = SlowAdapter("browser_skill", JobStatus.SUCCEEDED)
    fallback = ResultAdapter("ai_search_hub", JobStatus.SUCCEEDED)
    chain = AdapterChain(
        "gemini", [primary, fallback], attempt_timeouts=[0.01, 0.1]
    )

    result = await chain.run("gemini", ProbeRequest(prompt="hello"))

    assert result.status == JobStatus.SUCCEEDED
    assert result.adapter == "ai_search_hub"
    assert "browser_skill: hard timeout" in (result.diagnostic or "")
    assert primary.calls == fallback.calls == 1

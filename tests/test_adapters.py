import pytest

from look4geo_probe.adapters.base import ProbeAdapter
from look4geo_probe.adapters.registry import AdapterRegistry, AdapterChain, NoHealthyAdapter
from look4geo_probe.adapters.types import AdapterHealth
from look4geo_probe.models import JobStatus, PlatformAttempt, ProbeRequest


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
    def __init__(self, name, status, healthy=True):
        self.name = name
        self.status = status
        self.healthy = healthy
        self.calls = 0

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
        )

    async def login(self, platform):
        return {"adapter": self.name}


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

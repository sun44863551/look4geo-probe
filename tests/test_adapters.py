import pytest

from look4geo_probe.adapters.base import ProbeAdapter
from look4geo_probe.adapters.registry import AdapterRegistry, NoHealthyAdapter


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

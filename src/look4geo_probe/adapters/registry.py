from __future__ import annotations

from collections.abc import Mapping

from .base import ProbeAdapter


class NoHealthyAdapter(LookupError):
    pass


class AdapterRegistry:
    def __init__(
        self,
        adapters: Mapping[str, ProbeAdapter],
        platform_preferences: Mapping[str, list[str]],
    ):
        self.adapters = dict(adapters)
        self.platform_preferences = {
            platform: list(names) for platform, names in platform_preferences.items()
        }

    def resolve(self, platform: str, health: Mapping[str, bool]) -> ProbeAdapter:
        if platform not in self.platform_preferences:
            raise NoHealthyAdapter(f"unknown platform: {platform}")
        for adapter_name in self.platform_preferences[platform]:
            if health.get(adapter_name, False) and adapter_name in self.adapters:
                return self.adapters[adapter_name]
        raise NoHealthyAdapter(f"no healthy adapter for {platform}")


from __future__ import annotations

import asyncio
from collections.abc import Mapping

from .base import ProbeAdapter
from .types import AdapterHealth
from ..models import FailureKind, JobStatus, PlatformAttempt, ProbeRequest


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


class AdapterChain(ProbeAdapter):
    """Try configured adapters in order, exposing one platform adapter to the service."""

    name = "adapter_chain"

    def __init__(
        self,
        platform: str,
        adapters: list[ProbeAdapter],
        *,
        attempt_timeouts: list[float | None] | None = None,
    ):
        if not adapters:
            raise ValueError("adapter chain requires at least one adapter")
        self.platform = platform
        self.adapters = list(adapters)
        self.adapter_names = [adapter.name for adapter in adapters]
        self.attempt_timeouts = attempt_timeouts or [None] * len(adapters)
        if len(self.attempt_timeouts) != len(adapters):
            raise ValueError("attempt_timeouts must match adapters")

    async def health(self, platform: str) -> AdapterHealth:
        states = [await adapter.health(platform) for adapter in self.adapters]
        available = [
            adapter.name for adapter, state in zip(self.adapters, states, strict=True) if state.ok
        ]
        detail = "available: " + ", ".join(available) if available else "no healthy adapter"
        return AdapterHealth(bool(available), detail)

    async def run(self, platform: str, request: ProbeRequest) -> PlatformAttempt:
        failures: list[str] = []
        last_attempt: PlatformAttempt | None = None
        for adapter, hard_timeout in zip(
            self.adapters, self.attempt_timeouts, strict=True
        ):
            state = await adapter.health(platform)
            if not state.ok:
                failures.append(f"{adapter.name}: unavailable ({state.detail})")
                continue
            try:
                operation = adapter.run(platform, request)
                attempt = (
                    await asyncio.wait_for(operation, timeout=hard_timeout)
                    if hard_timeout is not None
                    else await operation
                )
            except asyncio.TimeoutError:
                attempt = PlatformAttempt(
                    platform=platform,
                    adapter=adapter.name,
                    status=JobStatus.FAILED,
                    failure=FailureKind.TIMEOUT,
                    diagnostic=f"hard timeout after {hard_timeout:g}s",
                )
            if attempt.status == JobStatus.SUCCEEDED:
                if failures:
                    return attempt.model_copy(
                        update={"diagnostic": "fallback after " + "; ".join(failures)}
                    )
                return attempt
            if attempt.failure in {
                FailureKind.LOGIN_REQUIRED,
                FailureKind.RATE_LIMITED,
            }:
                return attempt
            last_attempt = attempt
            failures.append(f"{adapter.name}: {attempt.diagnostic or attempt.status.value}")
        if last_attempt is not None:
            return last_attempt.model_copy(update={"diagnostic": "; ".join(failures)})
        return PlatformAttempt(
            platform=platform,
            adapter=self.name,
            status=JobStatus.FAILED,
            failure=FailureKind.UNKNOWN,
            diagnostic="; ".join(failures) or "no healthy adapter",
        )

    async def login(self, platform: str) -> dict:
        return await self.adapters[0].login(platform)

    async def cancel(self, job_id: str) -> None:
        for adapter in self.adapters:
            await adapter.cancel(job_id)

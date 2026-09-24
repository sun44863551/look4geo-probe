from __future__ import annotations

from .types import AdapterHealth
from ..models import PlatformAttempt, ProbeRequest


class ProbeAdapter:
    name = "base"

    async def health(self, platform: str) -> AdapterHealth:
        raise NotImplementedError

    async def login(self, platform: str) -> dict:
        raise NotImplementedError

    async def run(self, platform: str, request: ProbeRequest) -> PlatformAttempt:
        raise NotImplementedError

    async def cancel(self, job_id: str) -> None:
        raise NotImplementedError


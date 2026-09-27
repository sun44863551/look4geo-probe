from __future__ import annotations

import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .base import ProbeAdapter
from .types import AdapterHealth
from ..models import FailureKind, JobStatus, PlatformAttempt, ProbeRequest


@dataclass(frozen=True)
class GeminiBrowserResult:
    status: JobStatus
    answer: str = ""
    cited_links: tuple[tuple[str, str | None], ...] = field(default_factory=tuple)
    surfaced_links: tuple[tuple[str, str | None], ...] = field(default_factory=tuple)
    diagnostic: str | None = None
    failure: FailureKind | None = None
    artifact_paths: tuple[str, ...] = field(default_factory=tuple)


class GeminiBrowserRuntime(Protocol):
    async def health(self, profile_dir: Path) -> AdapterHealth: ...

    async def login(self, profile_dir: Path) -> dict: ...

    async def probe(
        self, profile_dir: Path, prompt: str, timeout: float, artifact_dir: Path
    ) -> GeminiBrowserResult: ...


class CamoufoxGeminiAdapter(ProbeAdapter):
    name = "camoufox_gemini"

    def __init__(
        self,
        runtime: GeminiBrowserRuntime,
        profile_dir: Path,
        artifact_root: Path,
        timeout: float = 120.0,
    ):
        self.runtime = runtime
        self.profile_dir = Path(profile_dir)
        self.artifact_root = Path(artifact_root)
        self.timeout = timeout

    @staticmethod
    def _require_gemini(platform: str) -> None:
        if platform != "gemini":
            raise ValueError("camoufox_gemini only supports gemini")

    def _profile_path_health(self) -> AdapterHealth | None:
        if self.profile_dir.exists():
            if not self.profile_dir.is_dir():
                return AdapterHealth(False, "profile path is not a directory")
            candidate = self.profile_dir
        else:
            candidate = self.profile_dir.parent
            while not candidate.exists() and candidate != candidate.parent:
                candidate = candidate.parent
        try:
            mode = candidate.stat().st_mode
        except OSError:
            return AdapterHealth(False, "profile parent is not accessible")
        if not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
            return AdapterHealth(False, "profile parent is not writable")
        return None

    async def health(self, platform: str) -> AdapterHealth:
        self._require_gemini(platform)
        path_failure = self._profile_path_health()
        if path_failure is not None:
            return path_failure
        return await self.runtime.health(self.profile_dir)

    async def login(self, platform: str) -> dict:
        self._require_gemini(platform)
        return await self.runtime.login(self.profile_dir)

    async def run(self, platform: str, request: ProbeRequest) -> PlatformAttempt:
        self._require_gemini(platform)
        raise NotImplementedError("Gemini browser result conversion is not implemented")

    async def cancel(self, job_id: str) -> None:
        return None

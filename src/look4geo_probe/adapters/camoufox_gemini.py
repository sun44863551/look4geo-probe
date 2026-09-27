from __future__ import annotations

import asyncio
import os
import stat
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .base import ProbeAdapter
from .types import AdapterHealth
from ..models import FailureKind, JobStatus, PlatformAttempt, ProbeRequest

GEMINI_URL = "https://gemini.google.com/app"
COMPOSER_SELECTOR = (
    'div[aria-label="Enter a prompt for Gemini"], '
    'rich-textarea [contenteditable="true"][role="textbox"]'
)
SEND_SELECTOR = 'button[aria-label*="Send"], button[aria-label*="发送"]'
STOP_SELECTOR = 'button[aria-label*="Stop"], button[aria-label*="停止"]'
ANSWER_SELECTOR = "message-content .markdown"
TURN_SELECTOR = "conversation-turn"
LOGIN_MARKERS = (
    "sign in to continue",
    "before you continue to google",
    "captcha",
    "unusual traffic",
    "异常流量",
)
RATE_LIMIT_MARKERS = ("reached your limit", "rate limit", "try again later", "达到上限")


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


class GeminiPageDriver:
    def __init__(self, page, *, poll_interval: float = 0.05):
        self.page = page
        self.poll_interval = poll_interval

    async def _body_text(self) -> str:
        try:
            return (await self.page.locator("body").inner_text()).casefold()
        except Exception:
            return ""

    async def _blocking_result(self) -> GeminiBrowserResult | None:
        body = await self._body_text()
        if any(marker in body for marker in RATE_LIMIT_MARKERS):
            return GeminiBrowserResult(
                status=JobStatus.FAILED,
                failure=FailureKind.RATE_LIMITED,
                diagnostic="Gemini rate limit detected",
            )
        if any(marker in body for marker in LOGIN_MARKERS):
            return GeminiBrowserResult(
                status=JobStatus.WAITING_FOR_LOGIN,
                failure=FailureKind.LOGIN_REQUIRED,
                diagnostic="Gemini requires manual login or verification",
            )
        return None

    async def login(self) -> dict:
        try:
            await self.page.goto(GEMINI_URL, wait_until="domcontentloaded")
        except TimeoutError:
            return {
                "platform": "gemini",
                "status": "user_action_required",
                "diagnostic": "Gemini navigation timed out",
            }
        blocked = await self._blocking_result()
        composer = self.page.locator(COMPOSER_SELECTOR).last
        if blocked is None and await composer.count():
            return {"platform": "gemini", "status": "succeeded"}
        return {
            "platform": "gemini",
            "status": "user_action_required",
            "diagnostic": (
                blocked.diagnostic if blocked else "Complete Gemini login in the browser window"
            ),
        }

    async def _fill_prompt(self, prompt: str) -> bool:
        for _ in range(2):
            try:
                await self.page.locator(COMPOSER_SELECTOR).last.fill(prompt)
            except Exception:
                continue
            current = await self.page.locator(COMPOSER_SELECTOR).last.inner_text()
            if current.strip() == prompt:
                return True
        return False

    async def _send(self) -> bool:
        before_url = self.page.url
        before_turns = await self.page.locator(TURN_SELECTOR).count()
        for _ in range(3):
            await self.page.locator(SEND_SELECTOR).last.click()
            composer = await self.page.locator(COMPOSER_SELECTOR).last.inner_text()
            if not composer.strip():
                return True
            if await self.page.locator(STOP_SELECTOR).count():
                return True
            if self.page.url != before_url:
                return True
            if await self.page.locator(TURN_SELECTOR).count() > before_turns:
                return True
            await asyncio.sleep(self.poll_interval)
        return False

    async def _wait_for_answer(self) -> str:
        previous = ""
        while True:
            answer = self.page.locator(ANSWER_SELECTOR).last
            text = (await answer.inner_text()).strip() if await answer.count() else ""
            generating = bool(await self.page.locator(STOP_SELECTOR).count())
            if text and text == previous and not generating:
                return text
            previous = text
            await asyncio.sleep(self.poll_interval)

    async def _probe(self, prompt: str, artifact_dir: Path) -> GeminiBrowserResult:
        await self.page.goto(GEMINI_URL, wait_until="domcontentloaded")
        blocked = await self._blocking_result()
        if blocked is not None:
            return blocked
        if not await self.page.locator(COMPOSER_SELECTOR).count():
            return GeminiBrowserResult(
                status=JobStatus.WAITING_FOR_LOGIN,
                failure=FailureKind.LOGIN_REQUIRED,
                diagnostic="Gemini composer is unavailable",
            )
        if not await self._fill_prompt(prompt):
            return GeminiBrowserResult(
                status=JobStatus.FAILED,
                failure=FailureKind.SEND_FAILED,
                diagnostic="Gemini prompt entry could not be verified",
            )
        if not await self._send():
            return GeminiBrowserResult(
                status=JobStatus.FAILED,
                failure=FailureKind.SEND_FAILED,
                diagnostic="Gemini submission state did not change after 3 attempts",
            )
        return GeminiBrowserResult(
            status=JobStatus.SUCCEEDED, answer=await self._wait_for_answer()
        )

    async def probe(
        self, prompt: str, timeout: float, artifact_dir: Path
    ) -> GeminiBrowserResult:
        try:
            async with asyncio.timeout(timeout):
                return await self._probe(prompt, artifact_dir)
        except TimeoutError:
            return GeminiBrowserResult(
                status=JobStatus.FAILED,
                failure=FailureKind.TIMEOUT,
                diagnostic=f"Gemini browser operation timed out after {timeout:g}s",
            )


class CamoufoxRuntime:
    def __init__(self, root: Path, headless: bool, browser: str | None):
        self.root = Path(root)
        self.headless = headless
        self.browser = browser

    @property
    def cache_dir(self) -> Path:
        return self.root / "data/camoufox/cache"

    @contextmanager
    def _local_cache(self):
        previous = os.environ.get("XDG_CACHE_HOME")
        os.environ["XDG_CACHE_HOME"] = str(self.cache_dir)
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("XDG_CACHE_HOME", None)
            else:
                os.environ["XDG_CACHE_HOME"] = previous

    async def health(self, profile_dir: Path) -> AdapterHealth:
        env = {**os.environ, "XDG_CACHE_HOME": str(self.cache_dir)}
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "camoufox",
                "version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
        except (OSError, TimeoutError):
            return AdapterHealth(False, "Camoufox runtime is unavailable")
        lines = (stdout or stderr).decode(errors="replace").strip().splitlines()
        return AdapterHealth(
            process.returncode == 0,
            lines[0] if lines else "Camoufox runtime is unavailable",
        )

    def _launch_options(self, profile_dir: Path) -> dict:
        options = {
            "persistent_context": True,
            "user_data_dir": str(profile_dir),
            "headless": self.headless,
            "os": "macos",
        }
        if self.browser:
            options["browser"] = self.browser
        return options

    @staticmethod
    async def _page(context):
        return context.pages[0] if context.pages else await context.new_page()

    async def login(self, profile_dir: Path) -> dict:
        profile_dir.mkdir(parents=True, exist_ok=True)
        with self._local_cache():
            from camoufox.async_api import AsyncCamoufox

            async with AsyncCamoufox(**self._launch_options(profile_dir)) as context:
                page = await self._page(context)
                return await GeminiPageDriver(page, poll_interval=0.5).login()

    async def probe(
        self, profile_dir: Path, prompt: str, timeout: float, artifact_dir: Path
    ) -> GeminiBrowserResult:
        profile_dir.mkdir(parents=True, exist_ok=True)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        with self._local_cache():
            from camoufox.async_api import AsyncCamoufox

            async with AsyncCamoufox(**self._launch_options(profile_dir)) as context:
                page = await self._page(context)
                return await GeminiPageDriver(page, poll_interval=0.5).probe(
                    prompt, timeout, artifact_dir
                )


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

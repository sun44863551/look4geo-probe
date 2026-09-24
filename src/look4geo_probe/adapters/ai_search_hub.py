from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .base import ProbeAdapter
from .types import AdapterHealth
from ..models import Citation, JobStatus, PlatformAttempt, ProbeRequest

SUPPORTED_PLATFORMS = {"doubao", "yuanbao", "qwen", "gemini", "grok"}
URL_PATTERN = re.compile(r"https?://[^\s<>\])}]+")


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class AsyncCommandRunner:
    async def run(self, command: list[str], timeout: float) -> CommandResult:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise
        return CommandResult(
            process.returncode,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )


class AIHubAdapter(ProbeAdapter):
    name = "ai_search_hub"

    def __init__(
        self,
        repository: Path,
        *,
        runner: AsyncCommandRunner | None = None,
        python_executable: str | None = None,
        default_timeout: float = 180.0,
    ):
        self.repository = Path(repository)
        self.runner = runner or AsyncCommandRunner()
        self.python_executable = python_executable or sys.executable
        self.default_timeout = default_timeout

    async def health(self, platform: str) -> AdapterHealth:
        script = self.repository / "scripts/run_web_chat.py"
        if platform not in SUPPORTED_PLATFORMS:
            return AdapterHealth(False, f"unsupported platform: {platform}")
        return AdapterHealth(script.exists(), str(script))

    async def login(self, platform: str) -> dict:
        return {
            "status": "user_action_required",
            "platform": platform,
            "instructions": "Complete login in the visible Chrome window, then retry the probe.",
        }

    async def run(self, platform: str, request: ProbeRequest) -> PlatformAttempt:
        if platform not in SUPPORTED_PLATFORMS:
            return PlatformAttempt(
                platform=platform,
                adapter=self.name,
                status=JobStatus.FAILED,
                diagnostic=f"unsupported platform: {platform}",
            )
        output = Path(
            str(request.options.get("output", self.repository / "out" / f"{platform}_answer.txt"))
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self.python_executable,
            str(self.repository / "scripts/run_web_chat.py"),
            "--site",
            platform,
            "--prompt",
            request.prompt,
            "--repo-root",
            str(self.repository),
            "--output",
            str(output),
        ]
        timeout = float(request.options.get("timeout", self.default_timeout))
        try:
            result = await self.runner.run(command, timeout)
        except asyncio.TimeoutError:
            return PlatformAttempt(
                platform=platform,
                adapter=self.name,
                status=JobStatus.FAILED,
                diagnostic="timeout",
            )

        diagnostic = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        if result.returncode != 0:
            login_required = any(
                marker in diagnostic.casefold()
                for marker in ("login required", "log in", "sign in", "登录")
            )
            return PlatformAttempt(
                platform=platform,
                adapter=self.name,
                status=(JobStatus.WAITING_FOR_LOGIN if login_required else JobStatus.FAILED),
                diagnostic=diagnostic or f"process exited {result.returncode}",
            )

        answer = output.read_text(encoding="utf-8") if output.exists() else result.stdout.strip()
        citations = [Citation(url=url) for url in dict.fromkeys(URL_PATTERN.findall(answer))]
        return PlatformAttempt(
            platform=platform,
            adapter=self.name,
            status=JobStatus.SUCCEEDED,
            raw_answer=answer,
            normalized_answer=answer.strip(),
            citations=citations,
            artifact_paths=[str(output)] if output.exists() else [],
            diagnostic=diagnostic or None,
        )

    async def cancel(self, job_id: str) -> None:
        return None

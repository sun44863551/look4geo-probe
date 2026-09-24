from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .base import ProbeAdapter
from .types import AdapterHealth
from ..models import Citation, JobStatus, PlatformAttempt, ProbeRequest

PLATFORMS = {
    "chatgpt": {
        "url": "https://chatgpt.com/",
        "textbox": "给 ChatGPT 发消息",
        "login_markers": ("登录", "注册"),
    },
    "deepseek": {
        "url": "https://chat.deepseek.com/",
        "textbox": "给 DeepSeek 发送消息",
        "login_markers": ("请输入手机号", "密码登录"),
    },
    "perplexity": {
        "url": "https://www.perplexity.ai/",
        "textbox": "输入 @ 以使用连接器",
        "login_markers": ("登录", "继续使用"),
    },
}
REF_PATTERN = re.compile(r"(@e\d+)\s+textbox\s+\"([^\"]+)\"")
URL_PATTERN = re.compile(r"https?://[^\s<>\])}]+")


@dataclass(frozen=True)
class BrowserProbeOutput:
    answer: str = ""
    citations: list[str] = field(default_factory=list)
    login_required: bool = False


class BskCliClient:
    def __init__(self, browser_instance_id: str, *, poll_interval: float = 2.0):
        if not browser_instance_id:
            raise ValueError("browser_instance_id is required")
        self.browser_instance_id = browser_instance_id
        self.poll_interval = poll_interval

    async def _run_json(self, *args: str, timeout: float = 30.0) -> dict | list:
        process = await asyncio.create_subprocess_exec(
            "bsk",
            *args,
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        if process.returncode != 0:
            raise RuntimeError(stderr.decode(errors="replace").strip() or "bsk command failed")
        return json.loads(stdout.decode("utf-8"))

    async def start(self, platform: str) -> str:
        result = await self._run_json(
            "session", "start", "--browser", self.browser_instance_id, "--no-focus"
        )
        return str(result["session_id"])

    async def probe(
        self, session_id: str, platform: str, prompt: str, timeout: float
    ) -> BrowserProbeOutput:
        config = PLATFORMS[platform]
        navigation = await self._run_json(
            "navigate", config["url"], "--session", session_id, timeout=timeout
        )
        observation = await self._run_json("observe", "--session", session_id, timeout=timeout)
        page_text = str(observation.get("text", ""))
        textbox_ref = self._find_textbox_ref(page_text, config["textbox"])
        if not textbox_ref:
            final_url = str(navigation.get("final_url", ""))
            if "sign_in" in final_url or any(
                marker in page_text for marker in config["login_markers"]
            ):
                return BrowserProbeOutput(login_required=True)
            raise RuntimeError(f"message textbox not found for {platform}")

        await self._run_json(
            "fill", textbox_ref, "--value", prompt, "--session", session_id, timeout=timeout
        )
        await self._run_json(
            "press", "Enter", "--ref", textbox_ref, "--session", session_id, timeout=timeout
        )

        deadline = asyncio.get_running_loop().time() + timeout
        previous = ""
        stable_rounds = 0
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(self.poll_interval)
            current = await self._run_json("observe", "--session", session_id, timeout=30)
            current_text = str(current.get("text", ""))
            if current_text != page_text and current_text == previous:
                stable_rounds += 1
                if stable_rounds >= 2:
                    return BrowserProbeOutput(
                        answer=current_text,
                        citations=list(dict.fromkeys(URL_PATTERN.findall(current_text))),
                    )
            else:
                stable_rounds = 0
            previous = current_text
        raise asyncio.TimeoutError

    async def stop(self, session_id: str) -> None:
        await self._run_json("session", "stop", session_id)

    @staticmethod
    def _find_textbox_ref(page_text: str, expected_label: str) -> str | None:
        for ref, label in REF_PATTERN.findall(page_text):
            if label == expected_label:
                return ref
        return None


class BrowserSkillAdapter(ProbeAdapter):
    name = "browser_skill"

    def __init__(self, *, client, artifact_root: Path, default_timeout: float = 180.0):
        self.client = client
        self.artifact_root = Path(artifact_root)
        self.default_timeout = default_timeout

    async def health(self, platform: str) -> AdapterHealth:
        return AdapterHealth(platform in PLATFORMS, "configured" if platform in PLATFORMS else "unsupported")

    async def login(self, platform: str) -> dict:
        return {
            "status": "user_action_required",
            "platform": platform,
            "instructions": f"Complete login at {PLATFORMS[platform]['url']} in the visible Agent Window.",
        }

    async def run(self, platform: str, request: ProbeRequest) -> PlatformAttempt:
        if platform not in PLATFORMS:
            return PlatformAttempt(
                platform=platform,
                adapter=self.name,
                status=JobStatus.FAILED,
                diagnostic=f"unsupported platform: {platform}",
            )
        session_id: str | None = None
        try:
            session_id = await self.client.start(platform)
            output = await self.client.probe(
                session_id,
                platform,
                request.prompt,
                float(request.options.get("timeout", self.default_timeout)),
            )
            if output.login_required:
                return PlatformAttempt(
                    platform=platform,
                    adapter=self.name,
                    status=JobStatus.WAITING_FOR_LOGIN,
                    diagnostic="login required",
                )
            return PlatformAttempt(
                platform=platform,
                adapter=self.name,
                status=JobStatus.SUCCEEDED,
                raw_answer=output.answer,
                normalized_answer=output.answer.strip(),
                citations=[Citation(url=url) for url in output.citations],
            )
        except asyncio.TimeoutError:
            return PlatformAttempt(
                platform=platform,
                adapter=self.name,
                status=JobStatus.FAILED,
                diagnostic="timeout",
            )
        except Exception as error:
            return PlatformAttempt(
                platform=platform,
                adapter=self.name,
                status=JobStatus.FAILED,
                diagnostic=str(error),
            )
        finally:
            if session_id is not None:
                try:
                    await self.client.stop(session_id)
                except Exception:
                    pass

    async def cancel(self, job_id: str) -> None:
        return None

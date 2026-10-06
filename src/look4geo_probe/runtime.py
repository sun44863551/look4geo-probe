from __future__ import annotations

import os
from pathlib import Path

from .adapters.base import ProbeAdapter
from .adapters.browser_skill import BskCliClient, BrowserSkillAdapter
from .adapters.camoufox_gemini import CamoufoxGeminiAdapter, CamoufoxRuntime
from .adapters.registry import AdapterChain
from .adapters.types import AdapterHealth
from .config import load_configuration
from .doctor import parse_camoufox_mode
from .models import FailureKind, JobStatus, PlatformAttempt, ProbeRequest
from .router import Router
from .service import ProbeService
from .storage import ProbeStore


class UnconfiguredAccountAdapter(ProbeAdapter):
    def __init__(self, platform: str, detail: str):
        self.name = f"unconfigured_{platform}"
        self.detail = detail

    async def health(self, platform: str) -> AdapterHealth:
        return AdapterHealth(False, self.detail)

    async def login(self, platform: str) -> dict:
        return {"platform": platform, "status": "user_action_required", "diagnostic": self.detail}

    async def run(self, platform: str, request: ProbeRequest) -> PlatformAttempt:
        return PlatformAttempt(
            platform=platform,
            adapter=self.name,
            status=JobStatus.FAILED,
            failure=FailureKind.LOGIN_REQUIRED,
            diagnostic=self.detail,
        )

    async def cancel(self, job_id: str) -> None:
        return None


def build_adapters(root: Path, runtime: str = "default") -> dict[str, object]:
    browser = BrowserSkillAdapter(
        client=BskCliClient(os.environ.get("LOOK4GEO_BROWSER_ID", "")),
        artifact_root=root / "data/runs",
    )
    del runtime  # Both callers intentionally use one machine-local adapter policy.
    chains = {
        "doubao": [browser],
        "deepseek": [browser],
        "yuanbao": [browser],
        "baidu": [browser],
        "chatgpt": [UnconfiguredAccountAdapter(
            "chatgpt", "Set LOOK4GEO_CHATGPT_BROWSER_ID to the dedicated Chrome profile extension instance ID"
        )],
        "gemini": [UnconfiguredAccountAdapter(
            "gemini", "Enable and sign in to the isolated Camoufox Gemini profile"
        )],
        "perplexity": [browser],
        # AI-Search-Hub resets a large debug profile during Grok fallback, which
        # is unsafe inside WorkBuddy's guarded filesystem. Keep Grok local-only.
        "grok": [browser],
    }
    shared_browser_id = os.environ.get("LOOK4GEO_BROWSER_ID", "")
    chatgpt_browser_id = os.environ.get("LOOK4GEO_CHATGPT_BROWSER_ID", "")
    if chatgpt_browser_id and chatgpt_browser_id != shared_browser_id:
        chains["chatgpt"] = [BrowserSkillAdapter(
            client=BskCliClient(chatgpt_browser_id),
            artifact_root=root / "data/runs",
        )]
    if parse_camoufox_mode(os.environ.get("LOOK4GEO_CAMOUFOX_ENABLED")) != "disabled":
        profile_dir = Path(
            os.environ.get(
                "LOOK4GEO_CAMOUFOX_PROFILE_DIR",
                root / "data/camoufox/profiles/gemini",
            )
        )
        camoufox = CamoufoxGeminiAdapter(
            CamoufoxRuntime(
                root,
                headless=os.environ.get("LOOK4GEO_CAMOUFOX_HEADLESS", "0") == "1",
                browser=os.environ.get(
                    "LOOK4GEO_CAMOUFOX_BROWSER", "152.0.4-beta.30"
                ),
                disable_content_sandbox=os.environ.get(
                    "LOOK4GEO_CAMOUFOX_DISABLE_CONTENT_SANDBOX", "0"
                ) == "1",
            ),
            profile_dir,
            root / "data/camoufox/artifacts",
        )
        chains["gemini"] = [camoufox]
    gemini_browser_id = os.environ.get("LOOK4GEO_GEMINI_BROWSER_ID", "")
    if gemini_browser_id and gemini_browser_id != shared_browser_id:
        chains["gemini"] = [BrowserSkillAdapter(
            client=BskCliClient(gemini_browser_id),
            artifact_root=root / "data/runs",
        )]
    return {
        platform: AdapterChain(platform, adapters)
        for platform, adapters in chains.items()
    }


def build_service(project_root: Path | None = None) -> ProbeService:
    root = project_root or Path(__file__).resolve().parents[2]
    platforms, routing = load_configuration(root / "config")
    router = Router(platforms, routing)
    adapters = build_adapters(root, os.environ.get("LOOK4GEO_RUNTIME", "default").casefold())
    store = ProbeStore(root / "data/probe.sqlite3", root / "data/runs")
    return ProbeService(router, store, adapters)

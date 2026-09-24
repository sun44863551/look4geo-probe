from __future__ import annotations

import os
from pathlib import Path

from .adapters.ai_search_hub import AIHubAdapter
from .adapters.browser_skill import BskCliClient, BrowserSkillAdapter
from .config import load_configuration
from .router import Router
from .service import ProbeService
from .storage import ProbeStore


def build_adapters(root: Path, runtime: str = "default") -> dict[str, object]:
    ai_hub = AIHubAdapter(root / "vendor/AI-Search-Hub")
    browser = BrowserSkillAdapter(
        client=BskCliClient(os.environ.get("LOOK4GEO_BROWSER_ID", "")),
        artifact_root=root / "data/runs",
    )
    if runtime == "workbuddy":
        return {
            platform: browser
            for platform in (
                "doubao", "deepseek", "yuanbao", "qwen",
                "chatgpt", "gemini", "perplexity", "grok",
            )
        }
    return {
        "doubao": ai_hub,
        "yuanbao": ai_hub,
        "qwen": ai_hub,
        "gemini": ai_hub,
        "grok": ai_hub,
        "deepseek": browser,
        "chatgpt": browser,
        "perplexity": browser,
    }


def build_service(project_root: Path | None = None) -> ProbeService:
    root = project_root or Path(__file__).resolve().parents[2]
    platforms, routing = load_configuration(root / "config")
    router = Router(platforms, routing)
    adapters = build_adapters(root, os.environ.get("LOOK4GEO_RUNTIME", "default").casefold())
    store = ProbeStore(root / "data/probe.sqlite3", root / "data/runs")
    return ProbeService(router, store, adapters)

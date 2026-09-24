from __future__ import annotations

import os
from pathlib import Path

from .adapters.ai_search_hub import AIHubAdapter
from .adapters.browser_skill import BskCliClient, BrowserSkillAdapter
from .config import load_configuration
from .router import Router
from .service import ProbeService
from .storage import ProbeStore


def build_service(project_root: Path | None = None) -> ProbeService:
    root = project_root or Path(__file__).resolve().parents[2]
    platforms, routing = load_configuration(root / "config")
    router = Router(platforms, routing)
    ai_hub = AIHubAdapter(root / "vendor/AI-Search-Hub")
    browser_id = os.environ.get("LOOK4GEO_BROWSER_ID", "")
    browser = BrowserSkillAdapter(
        client=BskCliClient(browser_id), artifact_root=root / "data/runs"
    )
    adapters = {
        "doubao": ai_hub,
        "yuanbao": ai_hub,
        "qwen": ai_hub,
        "gemini": ai_hub,
        "grok": ai_hub,
        "deepseek": browser,
        "chatgpt": browser,
        "perplexity": browser,
    }
    store = ProbeStore(root / "data/probe.sqlite3", root / "data/runs")
    return ProbeService(router, store, adapters)


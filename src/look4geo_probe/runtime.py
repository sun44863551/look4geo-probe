from __future__ import annotations

import os
from pathlib import Path

from .adapters.ai_search_hub import AIHubAdapter
from .adapters.browser_skill import BskCliClient, BrowserSkillAdapter
from .adapters.camoufox_gemini import CamoufoxGeminiAdapter, CamoufoxRuntime
from .adapters.registry import AdapterChain
from .config import load_configuration
from .doctor import parse_camoufox_mode
from .router import Router
from .service import ProbeService
from .storage import ProbeStore


def build_adapters(root: Path, runtime: str = "default") -> dict[str, object]:
    ai_hub = AIHubAdapter(root / "vendor/AI-Search-Hub")
    browser = BrowserSkillAdapter(
        client=BskCliClient(os.environ.get("LOOK4GEO_BROWSER_ID", "")),
        artifact_root=root / "data/runs",
    )
    del runtime  # Both callers intentionally use one machine-local adapter policy.
    chains = {
        "doubao": [browser, ai_hub],
        "deepseek": [browser],
        "yuanbao": [browser, ai_hub],
        "baidu": [browser],
        "chatgpt": [browser],
        "gemini": [browser, ai_hub],
        "perplexity": [browser],
        # AI-Search-Hub resets a large debug profile during Grok fallback, which
        # is unsafe inside WorkBuddy's guarded filesystem. Keep Grok local-only.
        "grok": [browser],
    }
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
        chains["gemini"].insert(0, camoufox)
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

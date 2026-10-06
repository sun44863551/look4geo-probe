import json
import asyncio
from pathlib import Path

import yaml

from look4geo_probe.runtime import build_adapters

ROOT = Path(__file__).parents[1]


def test_chatgpt_requires_an_explicit_probe_browser_and_never_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOK4GEO_BROWSER_ID", "daily-browser")
    for dedicated_id in ("", "daily-browser", "sampling-browser"):
        monkeypatch.setenv("LOOK4GEO_CHATGPT_BROWSER_ID", dedicated_id)
        adapters = build_adapters(tmp_path)
        chatgpt = adapters["chatgpt"]
        assert len(chatgpt.adapters) == 1
        client = chatgpt.adapters[0].client
        assert client.browser_instance_id == dedicated_id
        if not dedicated_id:
            assert asyncio.run(chatgpt.health("chatgpt")).ok is False
        assert adapters["deepseek"].adapters[0].client.browser_instance_id == "daily-browser"


def read_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, raw, _ = text.split("---", 2)
    return yaml.safe_load(raw)


def test_workbuddy_local_config_has_one_stdio_server_and_no_secret_literals():
    integration = ROOT / "connectors/workbuddy"
    mcp = json.loads((integration / "mcp.json").read_text(encoding="utf-8"))
    assert len(mcp["mcpServers"]) == 1
    server = mcp["mcpServers"]["look4geo-probe"]
    assert server["type"] == "stdio"
    assert server["args"] == ["-m", "look4geo_probe.mcp_server"]
    combined = json.dumps(mcp)
    assert "sk-" not in combined
    assert "Bearer " not in combined

    readme = (integration / "README.md").read_text(encoding="utf-8")
    assert "machine-local" in readme
    assert "must not be uploaded" in readme


def test_both_skills_have_discriminating_frontmatter():
    paths = [
        ROOT / "connectors/codex/SKILL.md",
        ROOT / "connectors/workbuddy/skills/look4geo-probe/SKILL.md",
    ]
    for path in paths:
        frontmatter = read_frontmatter(path)
        assert frontmatter["name"] == "look4geo-probe"
        assert "GEO" in frontmatter["description"]
        assert "probe" in path.read_text(encoding="utf-8").casefold()


def test_codex_and_workbuddy_use_same_adapter_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOK4GEO_BROWSER_ID", "browser-test")

    workbuddy = build_adapters(tmp_path, runtime="workbuddy")
    default = build_adapters(tmp_path, runtime="default")

    assert set(workbuddy) == {
        "doubao", "deepseek", "yuanbao", "baidu",
        "chatgpt", "gemini", "perplexity", "grok",
    }
    assert {
        platform: adapter.adapter_names for platform, adapter in workbuddy.items()
    } == {
        platform: adapter.adapter_names for platform, adapter in default.items()
    }


def test_shared_runtime_uses_browser_primary_and_ai_hub_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOK4GEO_BROWSER_ID", "browser-test")
    monkeypatch.delenv("LOOK4GEO_CAMOUFOX_ENABLED", raising=False)

    adapters = build_adapters(tmp_path, runtime="default")

    assert adapters["doubao"].adapter_names == ["browser_skill"]
    assert adapters["gemini"].adapter_names == ["camoufox_gemini"]
    assert adapters["chatgpt"].adapter_names == ["browser_skill"]
    assert adapters["chatgpt"].adapters[0].client.browser_instance_id == ""
    assert adapters["baidu"].adapter_names == ["browser_skill"]
    assert adapters["deepseek"].adapter_names == ["browser_skill"]
    assert adapters["yuanbao"].adapter_names == ["browser_skill"]
    assert adapters["perplexity"].adapter_names == ["browser_skill"]
    assert adapters["grok"].adapter_names == ["browser_skill"]
    assert "qwen" not in adapters


def test_disabling_camoufox_restores_previous_gemini_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOK4GEO_BROWSER_ID", "browser-test")
    monkeypatch.setenv("LOOK4GEO_CAMOUFOX_ENABLED", "0")

    adapters = build_adapters(tmp_path, runtime="default")

    assert adapters["gemini"].adapter_names == ["unconfigured_gemini"]


def test_gemini_camoufox_only_avoids_mixing_accounts(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOK4GEO_BROWSER_ID", "shared-browser")
    monkeypatch.setenv("LOOK4GEO_CAMOUFOX_ENABLED", "1")
    monkeypatch.setenv("LOOK4GEO_GEMINI_CAMOUFOX_ONLY", "1")

    adapters = build_adapters(tmp_path)

    assert adapters["gemini"].adapter_names == ["camoufox_gemini"]
    assert adapters["deepseek"].adapter_names == ["browser_skill"]


def test_camoufox_content_sandbox_setting_reaches_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOK4GEO_BROWSER_ID", "browser-test")
    monkeypatch.setenv("LOOK4GEO_CAMOUFOX_ENABLED", "1")
    monkeypatch.setenv("LOOK4GEO_CAMOUFOX_DISABLE_CONTENT_SANDBOX", "1")

    adapters = build_adapters(tmp_path)
    camoufox = next(
        adapter
        for adapter in adapters["gemini"].adapters
        if adapter.name == "camoufox_gemini"
    )

    assert adapters["gemini"].adapter_names == ["camoufox_gemini"]
    assert camoufox.runtime.disable_content_sandbox is True


def test_chatgpt_uses_only_dedicated_browser_instance(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOK4GEO_BROWSER_ID", "shared-browser")
    monkeypatch.setenv("LOOK4GEO_CHATGPT_BROWSER_ID", "dedicated-browser")

    adapters = build_adapters(tmp_path)

    assert adapters["chatgpt"].adapter_names == ["browser_skill"]
    assert adapters["chatgpt"].adapters[0].client.browser_instance_id == "dedicated-browser"
    assert adapters["deepseek"].adapters[0].client.browser_instance_id == "shared-browser"


def test_chatgpt_can_use_the_same_explicit_probe_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOK4GEO_BROWSER_ID", "shared-browser")
    monkeypatch.setenv("LOOK4GEO_CHATGPT_BROWSER_ID", "shared-browser")

    adapters = build_adapters(tmp_path)

    assert adapters["chatgpt"].adapter_names == ["browser_skill"]
    assert adapters["chatgpt"].adapters[0].client.browser_instance_id == "shared-browser"


def test_gemini_uses_only_dedicated_browser_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOK4GEO_BROWSER_ID", "shared-browser")
    monkeypatch.setenv("LOOK4GEO_GEMINI_BROWSER_ID", "dedicated-browser")
    monkeypatch.setenv("LOOK4GEO_CAMOUFOX_ENABLED", "1")

    adapters = build_adapters(tmp_path)

    assert adapters["gemini"].adapter_names == ["browser_skill", "camoufox_gemini"]
    assert adapters["gemini"].adapters[0].client.browser_instance_id == "dedicated-browser"


def test_gemini_can_use_the_same_explicit_probe_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOK4GEO_BROWSER_ID", "shared-browser")
    monkeypatch.setenv("LOOK4GEO_GEMINI_BROWSER_ID", "shared-browser")
    monkeypatch.setenv("LOOK4GEO_CAMOUFOX_ENABLED", "0")

    adapters = build_adapters(tmp_path)

    assert adapters["gemini"].adapter_names == ["browser_skill"]
    assert adapters["gemini"].adapters[0].client.browser_instance_id == "shared-browser"


def test_local_connector_instructions_preserve_private_cli_contract():
    codex = (ROOT / "connectors/codex/SKILL.md").read_text(encoding="utf-8")
    workbuddy = (
        ROOT / "connectors/workbuddy/skills/look4geo-probe/SKILL.md"
    ).read_text(encoding="utf-8")
    readme = (ROOT / "connectors/workbuddy/README.md").read_text(encoding="utf-8")

    for text in (codex, workbuddy):
        assert "scripts/probe-local" in text
        assert "--mode manual" in text
        assert "--platform" in text
        assert "source_capture_status" in text
        assert "quality_status" in text
        assert "--expected-term" in text
        assert "--timeout" in text
        assert "none_exposed" in text
        assert "failed" in text
        assert "camoufox_gemini" in text
        assert "scripts/probe-local login gemini --json" in text
        assert (
            'scripts/probe-local run "<question>" --mode manual '
            '--platform gemini --json'
        ) in text
        assert "machine-local" in text
        assert "login_required" in text
        assert "not mentioned" in text

    assert "public" in readme.casefold()
    assert "must not be uploaded" in readme
    assert "camoufox_gemini" in readme
    assert "data/camoufox/profiles/gemini" in readme


def test_ai_search_hub_is_a_pinned_submodule_initialized_by_bootstrap():
    gitmodules = (ROOT / ".gitmodules").read_text(encoding="utf-8")
    bootstrap = (ROOT / "scripts/bootstrap.sh").read_text(encoding="utf-8")
    patch_path = ROOT / "patches/ai-search-hub-gemini-native-input.patch"

    assert "vendor/AI-Search-Hub" in gitmodules
    assert "https://github.com/minsight-ai-info/AI-Search-Hub.git" in gitmodules
    assert "git submodule update --init --recursive vendor/AI-Search-Hub" in bootstrap
    assert "git clone https://github.com/minsight-ai-info/AI-Search-Hub.git" not in bootstrap
    assert patch_path.is_file()
    patch = patch_path.read_text(encoding="utf-8")
    assert 'if site_name == "gemini"' in patch
    assert "page.keyboard.type(question, delay=20)" in patch
    assert "ai-search-hub-gemini-native-input.patch" in bootstrap
    assert "apply --reverse --check" in bootstrap
    assert "apply --check" in bootstrap


def test_camoufox_is_pinned_and_bootstrapped_into_project_local_cache():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    bootstrap = (ROOT / "scripts/bootstrap-camoufox.sh").read_text(encoding="utf-8")
    local_env = (ROOT / "config/local.env.example").read_text(encoding="utf-8")

    assert 'camoufox = ["camoufox==0.5.6"]' in pyproject
    assert "XDG_CACHE_HOME" in bootstrap
    assert "data/camoufox/cache" in bootstrap
    assert "official/stable/152.0.4-beta.30" in bootstrap
    assert "sudo" not in bootstrap
    for name in (
        "LOOK4GEO_CAMOUFOX_ENABLED",
        "LOOK4GEO_CAMOUFOX_PROFILE_DIR",
        "LOOK4GEO_CAMOUFOX_BROWSER",
        "LOOK4GEO_CAMOUFOX_HEADLESS",
    ):
        assert name in local_env
    assert "password" not in local_env.casefold()

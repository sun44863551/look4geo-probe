import json
from pathlib import Path

import yaml

from look4geo_probe.runtime import build_adapters

ROOT = Path(__file__).parents[1]


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
        "doubao", "deepseek", "yuanbao", "qwen",
        "chatgpt", "gemini", "perplexity", "grok",
    }
    assert {
        platform: adapter.adapter_names for platform, adapter in workbuddy.items()
    } == {
        platform: adapter.adapter_names for platform, adapter in default.items()
    }


def test_shared_runtime_uses_browser_primary_and_ai_hub_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOK4GEO_BROWSER_ID", "browser-test")

    adapters = build_adapters(tmp_path, runtime="default")

    assert adapters["doubao"].adapter_names == ["browser_skill", "ai_search_hub"]
    assert adapters["gemini"].adapter_names == ["browser_skill", "ai_search_hub"]
    assert adapters["chatgpt"].adapter_names == ["browser_skill"]

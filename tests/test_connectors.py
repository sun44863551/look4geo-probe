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


def test_workbuddy_runtime_uses_existing_browser_for_all_platforms(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOK4GEO_BROWSER_ID", "browser-test")

    adapters = build_adapters(tmp_path, runtime="workbuddy")

    assert set(adapters) == {
        "doubao", "deepseek", "yuanbao", "qwen",
        "chatgpt", "gemini", "perplexity", "grok",
    }
    assert {adapter.name for adapter in adapters.values()} == {"browser_skill"}


def test_default_runtime_preserves_ai_search_hub_primaries(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOK4GEO_BROWSER_ID", "browser-test")

    adapters = build_adapters(tmp_path, runtime="default")

    assert adapters["doubao"].name == "ai_search_hub"
    assert adapters["gemini"].name == "ai_search_hub"
    assert adapters["chatgpt"].name == "browser_skill"

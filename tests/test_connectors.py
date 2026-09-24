import json
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def read_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, raw, _ = text.split("---", 2)
    return yaml.safe_load(raw)


def test_workbuddy_connector_has_one_stdio_server_and_no_secret_literals():
    connector = ROOT / "connectors/workbuddy"
    meta = json.loads((connector / "connector-meta.json").read_text(encoding="utf-8"))
    mcp = json.loads((connector / "mcp.json").read_text(encoding="utf-8"))
    assert meta["source"] == "look4geo-probe"
    assert meta["type"] == "mcp"
    assert len(mcp["mcpServers"]) == 1
    server = mcp["mcpServers"]["look4geo-probe"]
    assert server["type"] == "stdio"
    assert server["args"] == ["-m", "look4geo_probe.mcp_server"]
    combined = json.dumps({"meta": meta, "mcp": mcp})
    assert "sk-" not in combined
    assert "Bearer " not in combined


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


def test_workbuddy_package_contains_required_icon():
    icon = ROOT / "connectors/workbuddy/icon.svg"
    assert icon.exists()
    assert "<svg" in icon.read_text(encoding="utf-8")

from pathlib import Path

from look4geo_probe.doctor import collect_checks, summarize_checks


def test_required_failure_makes_doctor_unhealthy():
    checks = [
        {"name": "chrome", "required": True, "ok": True, "detail": "found"},
        {"name": "python", "required": True, "ok": False, "detail": "3.9"},
    ]
    assert summarize_checks(checks) == {"ok": False, "failed": ["python"]}


def test_optional_failure_does_not_make_doctor_unhealthy():
    checks = [{"name": "docker", "required": False, "ok": False, "detail": "missing"}]
    assert summarize_checks(checks) == {"ok": True, "failed": []}


def test_doctor_resolves_dependencies_from_project_not_callers_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    checks = {check["name"]: check for check in collect_checks()}

    expected_root = Path(__file__).resolve().parents[1]
    assert checks["ai_search_hub"]["detail"] == str(expected_root / "vendor/AI-Search-Hub")
    assert checks["promptfoo"]["detail"] == str(expected_root / "tools/promptfoo")


def test_doctor_uses_explicit_node_when_path_is_empty(tmp_path, monkeypatch):
    node = tmp_path / "node"
    node.write_text("#!/bin/sh\necho v22.22.2\n", encoding="utf-8")
    node.chmod(0o755)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("LOOK4GEO_NODE", str(node))

    checks = {check["name"]: check for check in collect_checks()}

    assert checks["node"]["ok"] is True
    assert checks["node"]["detail"] == "v22.22.2"


def test_doctor_reports_missing_explicit_node_without_crashing(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOK4GEO_NODE", str(tmp_path / "missing-node"))

    checks = {check["name"]: check for check in collect_checks()}

    assert checks["node"] == {
        "name": "node",
        "required": True,
        "ok": False,
        "detail": "missing",
    }

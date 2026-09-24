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

from pathlib import Path

import pytest

from look4geo_probe.doctor import (
    collect_camoufox_check,
    collect_checks,
    parse_camoufox_mode,
    summarize_checks,
)


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
    assert checks["promptfoo"]["path"] == str(expected_root / "tools/promptfoo")


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


def test_doctor_marks_promptfoo_unhealthy_when_cli_cannot_start(tmp_path):
    promptfoo = tmp_path / "tools/promptfoo/node_modules/.bin/promptfoo"
    promptfoo.parent.mkdir(parents=True)
    promptfoo.write_text("#!/bin/sh\necho cannot-start >&2\nexit 7\n", encoding="utf-8")
    promptfoo.chmod(0o755)

    checks = {check["name"]: check for check in collect_checks(project_root=tmp_path)}

    assert checks["promptfoo"]["ok"] is False
    assert checks["promptfoo"]["detail"] == "cannot-start"


def test_doctor_survives_a_hanging_promptfoo_instead_of_crashing(tmp_path, monkeypatch):
    """A slow CLI must degrade to a readable failure, not a TimeoutExpired crash."""
    promptfoo = tmp_path / "tools/promptfoo/node_modules/.bin/promptfoo"
    promptfoo.parent.mkdir(parents=True)
    promptfoo.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    promptfoo.chmod(0o755)
    monkeypatch.setattr("look4geo_probe.doctor.PROMPTFOO_VERSION_TIMEOUT", 1)

    checks = {check["name"]: check for check in collect_checks(project_root=tmp_path)}

    assert checks["promptfoo"]["ok"] is False
    assert checks["promptfoo"]["detail"] == "timeout after 1s"


def test_doctor_runs_promptfoo_with_project_local_config_dir(tmp_path):
    promptfoo = tmp_path / "tools/promptfoo/node_modules/.bin/promptfoo"
    promptfoo.parent.mkdir(parents=True)
    promptfoo.write_text(
        "#!/bin/sh\n"
        'test "$PROMPTFOO_CONFIG_DIR" = "$EXPECTED_CONFIG_DIR" || exit 9\n'
        "echo 0.123.1\n",
        encoding="utf-8",
    )
    promptfoo.chmod(0o755)

    expected = tmp_path / "data/promptfoo"
    env = {"EXPECTED_CONFIG_DIR": str(expected)}
    checks = {
        check["name"]: check
        for check in collect_checks(project_root=tmp_path, extra_env=env)
    }

    assert checks["promptfoo"] == {
        "name": "promptfoo",
        "required": True,
        "ok": True,
        "detail": "0.123.1",
        "path": str(tmp_path / "tools/promptfoo"),
    }


def test_camoufox_mode_has_exact_three_state_semantics():
    assert parse_camoufox_mode(None) == "auto"
    assert parse_camoufox_mode("0") == "disabled"
    assert parse_camoufox_mode("1") == "required"
    with pytest.raises(ValueError, match="LOOK4GEO_CAMOUFOX_ENABLED"):
        parse_camoufox_mode("yes")


def test_disabled_camoufox_is_optional_and_healthy(tmp_path):
    check = collect_camoufox_check(
        tmp_path, {"LOOK4GEO_CAMOUFOX_ENABLED": "0"}
    )

    assert check == {
        "name": "camoufox",
        "required": False,
        "ok": True,
        "detail": "disabled",
        "mode": "disabled",
        "profile_path": str(tmp_path / "data/camoufox/profiles/gemini"),
        "cache_path": str(tmp_path / "data/camoufox/cache"),
    }


def test_invalid_camoufox_mode_is_a_required_readable_failure(tmp_path):
    check = collect_camoufox_check(
        tmp_path, {"LOOK4GEO_CAMOUFOX_ENABLED": "sometimes"}
    )

    assert check["required"] is True
    assert check["ok"] is False
    assert check["mode"] == "invalid"
    assert "LOOK4GEO_CAMOUFOX_ENABLED" in check["detail"]

import os
from pathlib import Path
import subprocess


def run_launcher(tmp_path, requested, *, locked=True):
    root = Path(__file__).resolve().parents[1]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    launcher = scripts / "probe-local"
    launcher.write_text((root / "scripts/probe-local").read_text())
    config = tmp_path / "config"
    config.mkdir()
    policy = "LOOK4GEO_LOCKED_BROWSER_ID=81483588\n" if locked else ""
    (config / "local.env").write_text(policy + "LOOK4GEO_BROWSER_ID=81483588\nLOOK4GEO_CHATGPT_BROWSER_ID=81483588\nLOOK4GEO_GEMINI_BROWSER_ID=81483588\n")
    binaries = tmp_path / ".venv/bin"
    binaries.mkdir(parents=True)
    probe = binaries / "probe"
    probe.write_text('#!/bin/sh\nprintf "%s" "$LOOK4GEO_BROWSER_ID"\n')
    probe.chmod(0o700)
    return subprocess.run(["sh", str(launcher)], capture_output=True, text=True,
                          env={**os.environ, "LOOK4GEO_BROWSER_ID": requested})


def test_locked_profile_rejects_stale_caller_override(tmp_path):
    result = run_launcher(tmp_path, "old-browser")
    assert result.returncode != 0
    assert result.stdout == ""
    assert "81483588" in result.stderr


def test_locked_profile_accepts_explicit_matching_instance(tmp_path):
    result = run_launcher(tmp_path, "81483588")
    assert result.returncode == 0
    assert result.stdout == "81483588"


def test_unlocked_installation_keeps_explicit_override_compatibility(tmp_path):
    result = run_launcher(tmp_path, "other-browser", locked=False)
    assert result.returncode == 0
    assert result.stdout == "other-browser"

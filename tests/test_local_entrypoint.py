import os
from pathlib import Path
import subprocess


def run_launcher(tmp_path, requested, *, locked=True, gemini_id="81483588", gemini_lock=None):
    root = Path(__file__).resolve().parents[1]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    launcher = scripts / "probe-local"
    launcher.write_text((root / "scripts/probe-local").read_text())
    config = tmp_path / "config"
    config.mkdir()
    policy = "LOOK4GEO_LOCKED_BROWSER_ID=81483588\n" if locked else ""
    if gemini_lock is not None:
        policy += f"LOOK4GEO_LOCKED_GEMINI_BROWSER_ID={gemini_lock}\n"
    (config / "local.env").write_text(
        policy + "LOOK4GEO_BROWSER_ID=81483588\nLOOK4GEO_CHATGPT_BROWSER_ID=81483588\n"
        + f"LOOK4GEO_GEMINI_BROWSER_ID={gemini_id}\n"
    )
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


def test_locked_profile_accepts_separately_locked_gemini(tmp_path):
    result = run_launcher(tmp_path, "81483588", gemini_id="297033a5", gemini_lock="297033a5")
    assert result.returncode == 0


def test_locked_profile_rejects_wrong_gemini_instance(tmp_path):
    result = run_launcher(tmp_path, "81483588", gemini_lock="297033a5")
    assert result.returncode != 0
    assert "297033a5" in result.stderr


def test_explicit_lane_config_binds_all_platforms(tmp_path):
    root = Path(__file__).resolve().parents[1]
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    launcher = scripts / 'probe-local'
    launcher.write_text((root / 'scripts/probe-local').read_text())
    config = tmp_path / 'lane.env'
    config.write_text('LOOK4GEO_LOCKED_BROWSER_ID=lane-browser\nLOOK4GEO_BROWSER_ID=lane-browser\nLOOK4GEO_CHATGPT_BROWSER_ID=lane-browser\nLOOK4GEO_GEMINI_BROWSER_ID=lane-browser\n')
    binaries = tmp_path / '.venv/bin'
    binaries.mkdir(parents=True)
    probe = binaries / 'probe'
    probe.write_text('#!/bin/sh\nprintf "%s,%s,%s" "$LOOK4GEO_BROWSER_ID" "$LOOK4GEO_CHATGPT_BROWSER_ID" "$LOOK4GEO_GEMINI_BROWSER_ID"\n')
    probe.chmod(0o700)
    result = subprocess.run(['sh', str(launcher)], capture_output=True, text=True,
                            env={**os.environ, 'LOOK4GEO_BROWSER_ID': 'lane-browser', 'LOOK4GEO_LOCAL_ENV': str(config)})
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'lane-browser,lane-browser,lane-browser'

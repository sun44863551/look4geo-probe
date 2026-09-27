from pathlib import Path
import subprocess


def test_promptfoo_wrapper_uses_project_local_state(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    fake = tmp_path / "promptfoo"
    fake.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n%s\\n" "$PROMPTFOO_CONFIG_DIR" "$PROMPTFOO_DISABLE_TELEMETRY"\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    state_dir = tmp_path / "state"

    completed = subprocess.run(
        [str(project_root / "scripts/promptfoo-local"), "--version"],
        env={
            "PATH": "/usr/bin:/bin",
            "LOOK4GEO_PROMPTFOO_BIN": str(fake),
            "LOOK4GEO_PROMPTFOO_CONFIG_DIR": str(state_dir),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout.splitlines() == [str(state_dir), "1"]

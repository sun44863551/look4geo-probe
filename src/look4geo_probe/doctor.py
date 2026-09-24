from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def summarize_checks(checks: list[dict]) -> dict:
    failed = [check["name"] for check in checks if check["required"] and not check["ok"]]
    return {"ok": not failed, "failed": failed}


def _command_version(
    name: str, args: list[str], *, executable: str | None = None
) -> tuple[bool, str]:
    executable = executable or shutil.which(name)
    if not executable:
        return False, "missing"
    completed = subprocess.run(
        [executable, *args], capture_output=True, text=True, timeout=10, check=False
    )
    detail = (completed.stdout or completed.stderr).strip().splitlines()
    return completed.returncode == 0, detail[0] if detail else executable


def collect_checks(project_root: Path | None = None) -> list[dict]:
    root = project_root or Path(__file__).resolve().parents[2]
    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    checks: list[dict] = []
    checks.append(
        {
            "name": "python",
            "required": True,
            "ok": sys.version_info[:2] == (3, 12),
            "detail": sys.version.split()[0],
        }
    )
    for name, args, required, executable in (
        ("node", ["--version"], True, os.environ.get("LOOK4GEO_NODE")),
        ("git", ["--version"], True, None),
        ("bsk", ["--version"], True, None),
        ("docker", ["--version"], False, None),
    ):
        ok, detail = _command_version(name, args, executable=executable)
        checks.append({"name": name, "required": required, "ok": ok, "detail": detail})
    checks.extend(
        [
            {"name": "chrome", "required": True, "ok": chrome.exists(), "detail": str(chrome)},
            {
                "name": "ai_search_hub",
                "required": True,
                "ok": (root / "vendor/AI-Search-Hub/scripts/run_web_chat.py").exists(),
                "detail": str(root / "vendor/AI-Search-Hub"),
            },
            {
                "name": "promptfoo",
                "required": True,
                "ok": (root / "tools/promptfoo/node_modules/.bin/promptfoo").exists(),
                "detail": str(root / "tools/promptfoo"),
            },
        ]
    )
    return checks


def main() -> int:
    checks = collect_checks()
    print(json.dumps({"summary": summarize_checks(checks), "checks": checks}, ensure_ascii=False))
    return 0 if summarize_checks(checks)["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

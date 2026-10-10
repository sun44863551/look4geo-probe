"""Exercise installed drivers with fake local subprocesses, never customer jobs."""
import collections
import json
from pathlib import Path
import subprocess
import sys
import pytest

DRIVERS = Path("/Users/sunkai/WorkBuddy/hanyu/HANYU_GEO_S1/RUN_S3")


@pytest.mark.parametrize("name,args", [
    ("run_dcta100.sh", ["chatgpt", "3"]),
    ("run_jp100.sh", ["TEST", "chatgpt"]),
    ("run_sku100.sh", ["TEST"]),
    ("run_chatgpt_arm.sh", []),
    ("run_full_600.sh", []),
])
def test_driver_does_not_unblock_and_resubmit(tmp_path, name, args):
    source = DRIVERS / name
    if not source.exists():
        pytest.skip("Machine-local driver is not installed")
    driver = tmp_path / name
    driver.write_text(source.read_text().replace(
        "PY=/Users/sunkai/.workbuddy/binaries/python/versions/3.13.12/bin/python3",
        f"PY={sys.executable}",
    ))
    (tmp_path / "run_batch_s3.py").write_text(
        "import sys,json\nfrom pathlib import Path\n"
        "with Path(__file__).with_name('calls').open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "print('PLATFORM BLOCKED: rate_limited')\n"
    )
    (tmp_path / "_check_left.py").write_text("print(10)\n")
    (tmp_path / "reset_rerun_s3.py").write_text(
        "from pathlib import Path\nPath(__file__).with_name('unblocked').touch()\n"
    )
    result = subprocess.run(["sh", str(driver), *args], cwd=tmp_path, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr.decode()
    assert not (tmp_path / "unblocked").exists()
    calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
    platforms = collections.Counter(call[call.index("--platforms") + 1] for call in calls)
    assert max(platforms.values()) == 1
    assert all(call[call.index("--sleep") + 1] == "30" for call in calls)

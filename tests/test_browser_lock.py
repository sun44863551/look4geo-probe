import asyncio
import json
import sys

import pytest

from look4geo_probe.browser_lock import BrowserOperationLock


@pytest.mark.asyncio
async def test_second_browser_operation_waits_for_first(tmp_path):
    first = BrowserOperationLock(tmp_path / "browser.lock", poll_interval=0.01)
    second = BrowserOperationLock(tmp_path / "browser.lock", poll_interval=0.01)
    entered = asyncio.Event()

    async with first:
        task = asyncio.create_task(second.__aenter__())
        await asyncio.sleep(0.03)
        assert not task.done()
    await asyncio.wait_for(task, timeout=0.2)
    entered.set()
    assert entered.is_set()
    await second.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_browser_operation_lock_releases_after_exception(tmp_path):
    path = tmp_path / "browser.lock"
    with pytest.raises(RuntimeError):
        async with BrowserOperationLock(path, poll_interval=0.01):
            raise RuntimeError("boom")

    async with BrowserOperationLock(path, poll_interval=0.01):
        assert True


@pytest.mark.asyncio
async def test_cancelled_waiter_closes_its_file_handle(tmp_path, monkeypatch):
    path = tmp_path / "browser.lock"
    opened = []
    original = type(path).open

    def track_open(self, *args, **kwargs):
        handle = original(self, *args, **kwargs)
        if self == path:
            opened.append(handle)
        return handle

    monkeypatch.setattr(type(path), "open", track_open)
    async with BrowserOperationLock(path, poll_interval=0.01):
        lock = BrowserOperationLock(path, poll_interval=0.01)
        task = asyncio.create_task(lock.__aenter__())
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert opened[-1].closed


@pytest.mark.asyncio
async def test_three_processes_three_rounds_never_overlap(tmp_path):
    script = """
import asyncio, json, sys
from pathlib import Path
from look4geo_probe.browser_lock import BrowserOperationLock
async def main():
    lock = BrowserOperationLock(Path(sys.argv[1]), poll_interval=0.005)
    for _ in range(3):
        async with lock:
            with open(sys.argv[2], 'a') as output:
                output.write(json.dumps(['enter', sys.argv[3]])+'\\n')
            await asyncio.sleep(0.02)
            with open(sys.argv[2], 'a') as output:
                output.write(json.dumps(['exit', sys.argv[3]])+'\\n')
asyncio.run(main())
"""
    log = tmp_path / "events.jsonl"
    processes = [await asyncio.create_subprocess_exec(
        sys.executable, "-c", script, str(tmp_path / "shared.lock"), str(log), str(index),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    ) for index in range(3)]
    for process, (_, stderr) in zip(processes, await asyncio.gather(*(p.communicate() for p in processes))):
        assert process.returncode == 0, stderr.decode()
    events = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(events) == 18
    for enter, leave in zip(events[::2], events[1::2]):
        assert enter[0] == "enter" and leave == ["exit", enter[1]]

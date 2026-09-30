import asyncio

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

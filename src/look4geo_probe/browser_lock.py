from __future__ import annotations

import asyncio
import fcntl
from pathlib import Path
from typing import IO


class BrowserOperationLock:
    """Serialize access to the shared local Agent Window across processes."""

    def __init__(self, path: Path, *, poll_interval: float = 0.2):
        self.path = Path(path)
        self.poll_interval = poll_interval
        self._handle: IO[str] | None = None

    async def __aenter__(self) -> "BrowserOperationLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._handle = handle
                return self
            except BlockingIOError:
                await asyncio.sleep(self.poll_interval)

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self._handle is None:
            return
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None

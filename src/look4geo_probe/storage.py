from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import JobStatus, ProbeRequest


@dataclass(frozen=True)
class StoredJob:
    job_id: str
    request: ProbeRequest
    status: JobStatus
    artifact_dir: Path
    diagnostic: str | None = None


class ProbeStore:
    def __init__(self, database_path: Path, runs_dir: Path):
        self.database_path = Path(database_path)
        self.runs_dir = Path(runs_dir)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    request_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    artifact_dir TEXT NOT NULL UNIQUE,
                    diagnostic TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def create_job(self, request: ProbeRequest) -> StoredJob:
        job_id = str(uuid.uuid4())
        artifact_dir = self.runs_dir / job_id
        artifact_dir.mkdir(mode=0o700)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    request.model_dump_json(),
                    JobStatus.QUEUED.value,
                    str(artifact_dir),
                    None,
                    now,
                    now,
                ),
            )
        return StoredJob(job_id, request, JobStatus.QUEUED, artifact_dir)

    def get_job(self, job_id: str) -> StoredJob:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return StoredJob(
            job_id=row["job_id"],
            request=ProbeRequest.model_validate_json(row["request_json"]),
            status=JobStatus(row["status"]),
            artifact_dir=Path(row["artifact_dir"]),
            diagnostic=row["diagnostic"],
        )

    def update_status(
        self, job_id: str, status: JobStatus, diagnostic: str | None = None
    ) -> StoredJob:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET status = ?, diagnostic = ?, updated_at = ? WHERE job_id = ?",
                (status.value, diagnostic, now, job_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(job_id)
        return self.get_job(job_id)

    def recover_orphans(self) -> list[str]:
        active = (JobStatus.ROUTING.value, JobStatus.RUNNING.value)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT job_id FROM jobs WHERE status IN (?, ?)", active
            ).fetchall()
        recovered = [row["job_id"] for row in rows]
        for job_id in recovered:
            self.update_status(
                job_id, JobStatus.FAILED, "interrupted: worker stopped before completion"
            )
        return recovered


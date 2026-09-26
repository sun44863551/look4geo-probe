from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 2


class RouteMode(StrEnum):
    AUTO = "auto"
    COMPARE = "compare"
    ALL = "all"
    MANUAL = "manual"


class JobStatus(StrEnum):
    QUEUED = "queued"
    ROUTING = "routing"
    RUNNING = "running"
    WAITING_FOR_LOGIN = "waiting_for_login"
    PARTIAL = "partial"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FailureKind(StrEnum):
    LOGIN_REQUIRED = "login_required"
    RATE_LIMITED = "rate_limited"
    SEND_FAILED = "send_failed"
    EXTRACTION_FAILED = "extraction_failed"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


class SourceRole(StrEnum):
    CITED = "cited"
    SURFACED = "surfaced"


class SourceEvidenceOrigin(StrEnum):
    ANSWER_DOM = "answer_dom"
    SOURCE_PANEL = "source_panel"


class SourceCaptureStatus(StrEnum):
    CAPTURED = "captured"
    NONE_EXPOSED = "none_exposed"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProbeRequest(StrictModel):
    prompt: str = Field(min_length=1)
    mode: RouteMode = RouteMode.AUTO
    platforms: list[str] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)
    repeats: int = Field(default=1, ge=1, le=10)

    @field_validator("prompt")
    @classmethod
    def normalize_prompt(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("prompt must not be blank")
        return value

    @field_validator("platforms")
    @classmethod
    def deduplicate_platforms(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @model_validator(mode="after")
    def validate_mode(self) -> "ProbeRequest":
        if self.mode == RouteMode.MANUAL and not self.platforms:
            raise ValueError("manual mode requires at least one platform")
        return self


class PlatformHealth(StrictModel):
    available: bool = True
    logged_in: bool = True
    rate_limited: bool = False
    reason: str | None = None


class RoutingDecision(StrictModel):
    selected_platforms: list[str] = Field(default_factory=list)
    reasons: dict[str, list[str]] = Field(default_factory=dict)
    excluded: dict[str, str] = Field(default_factory=dict)
    coverage_gaps: list[str] = Field(default_factory=list)
    domestic_count: int = 0
    international_count: int = 0


class Citation(StrictModel):
    url: str
    label: str | None = None


class SourceRecord(StrictModel):
    url: str
    title: str | None = None
    domain: str
    snippet: str | None = None
    source_role: SourceRole
    evidence_origin: SourceEvidenceOrigin
    linked_in_answer: bool


class PlatformAttempt(StrictModel):
    platform: str
    adapter: str
    status: JobStatus
    raw_answer: str = ""
    normalized_answer: str = ""
    citations: list[Citation] = Field(default_factory=list)
    sources: list[SourceRecord] = Field(default_factory=list)
    source_capture_status: SourceCaptureStatus = SourceCaptureStatus.NONE_EXPOSED
    source_capture_diagnostic: str | None = None
    diagnostic: str | None = None
    failure: FailureKind | None = None
    query_original: str = ""
    query_sent: str = ""
    query_normalized: bool = False
    sample_index: int = 1
    artifact_paths: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None


class ProbeResult(StrictModel):
    schema_version: int = SCHEMA_VERSION
    job_id: str
    prompt: str
    status: JobStatus
    routing: RoutingDecision | None = None
    attempts: list[PlatformAttempt] = Field(default_factory=list)
    diagnostic: str | None = None

"""Observability schemas — unified execution result and domain status models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ExecutionDomain = Literal["task", "application", "container", "file"]
ExecutionStatus = Literal["ok", "warning", "failed", "timeout", "skipped", "running"]

# Activity Timeline event types — Phase 2 unified event taxonomy
ActivityEventType = Literal[
    "execution_started",
    "execution_succeeded",
    "execution_failed",
    "item_added",
    "item_deleted",
    "item_uploaded",
    "item_skipped",
    "container_abnormal",
    "task_failed",
    "plugin_failed",
]


class ExecutionCounters(BaseModel):
    added: int = 0
    deleted: int = 0
    uploaded: int = 0
    skipped: int = 0
    failed: int = 0
    unchanged: int = 0
    pending: int = 0


class UnifiedExecutionResult(BaseModel):
    execution_id: str
    domain: ExecutionDomain
    source_slug: str
    source_name: str
    trigger: str = "unknown"
    status: ExecutionStatus
    event_type: ActivityEventType | None = None  # Phase 2: explicit event taxonomy
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: int | None = None
    counters: ExecutionCounters = Field(default_factory=ExecutionCounters)
    skip_reasons: list[str] = Field(default_factory=list)
    failure_reasons: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class UnifiedExecutionFeed(BaseModel):
    generated_at: datetime
    hours: int
    total: int
    items: list[UnifiedExecutionResult]


class ActivityTimelineEntry(BaseModel):
    """Phase 2: Dedicated activity timeline event with source tagging."""
    id: str
    timestamp: datetime
    event_type: ActivityEventType
    domain: ExecutionDomain
    source: str
    summary: str = ""
    counters: ExecutionCounters = Field(default_factory=ExecutionCounters)
    details: dict[str, object] = Field(default_factory=dict)


class ActivityTimeline(BaseModel):
    """Phase 2: Paginated activity timeline response."""
    generated_at: datetime
    hours: int
    total: int
    events: list[ActivityTimelineEntry]


class TaskDomainStatus(BaseModel):
    success_24h: int = 0
    failed_24h: int = 0
    timeout_24h: int = 0
    running_now: int = 0
    pending_count: int = 0


class ApplicationDomainStatus(BaseModel):
    ok_24h: int = 0
    failed_24h: int = 0
    skipped_24h: int = 0
    counters_24h: ExecutionCounters = Field(default_factory=ExecutionCounters)


class ContainerDomainStatus(BaseModel):
    running: int = 0
    stopped: int = 0
    error: int = 0
    abnormal_containers: list[str] = Field(default_factory=list)


class FileDomainStatus(BaseModel):
    storage_usage_percent: float = 0.0
    uploaded_success_24h: int = 0
    uploaded_failed_24h: int = 0
    deleted_24h: int = 0
    recent_changes_24h: int = 0


class ObservabilityOverview(BaseModel):
    generated_at: datetime
    task: TaskDomainStatus
    application: ApplicationDomainStatus
    container: ContainerDomainStatus
    file: FileDomainStatus

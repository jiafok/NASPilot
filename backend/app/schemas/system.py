"""System schemas — stats, logs, settings."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SystemStats(BaseModel):
    """Dashboard system resource stats."""
    cpu_percent: float
    cpu_count: int
    memory_total: int
    memory_used: int
    memory_percent: float
    disk_total: int
    disk_used: int
    disk_percent: float
    docker_status: dict[str, Any] = {}
    qbittorrent_status: dict[str, Any] = {}
    alist_status: dict[str, Any] = {}


class LogEntryOut(BaseModel):
    id: int
    timestamp: datetime
    logger: str
    level: str
    source: str | None = None
    message: str
    extra: dict[str, Any] | None = None

    model_config = {"from_attributes": True}


class SettingOut(BaseModel):
    key: str
    value: str
    value_type: str
    description: str | None = None
    category: str
    is_public: bool


class SettingUpdate(BaseModel):
    value: str


class PaginatedResponse(BaseModel):
    """Generic paginated response wrapper."""
    items: list[Any]
    total: int
    page: int
    page_size: int

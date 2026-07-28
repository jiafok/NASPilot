"""System schemas — stats, logs, settings."""

from datetime import datetime
from typing import Any
from typing import Literal

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


class SettingBulkEntry(BaseModel):
    key: str
    value: str


class PaginatedResponse(BaseModel):
    """Generic paginated response wrapper."""
    items: list[Any]
    total: int
    page: int
    page_size: int


class DockerContainerOut(BaseModel):
    id: str
    short_id: str
    name: str
    image: str
    status: str
    state: str
    running: bool
    created_at: str | None = None
    stack: str = ""
    ownership: str = ""
    ip_addresses: list[str] = Field(default_factory=list)
    ports: list[str] = Field(default_factory=list)


class DockerExecRequest(BaseModel):
    command: str = Field(min_length=1)
    user: str | None = None
    workdir: str | None = None


class DockerExecResult(BaseModel):
    exit_code: int | None = None
    running: bool = False
    output: str = ""


class DockerActionRequest(BaseModel):
    action: Literal["start", "stop", "restart", "pause", "unpause", "kill", "remove"]


class DockerBulkActionRequest(BaseModel):
    action: Literal["start", "stop", "restart", "pause", "unpause", "kill", "remove"]
    container_ids: list[str] = Field(default_factory=list)


class DockerStatsOut(BaseModel):
    id: str
    short_id: str
    name: str
    cpu_percent: float
    memory_usage: int
    memory_limit: int
    memory_percent: float
    net_rx: int
    net_tx: int
    blk_read: int
    blk_write: int
    pids: int

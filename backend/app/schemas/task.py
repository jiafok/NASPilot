"""Task schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TaskBase(BaseModel):
    name: str = Field(..., max_length=128)
    description: str | None = None
    task_type: str = Field(..., pattern="^(shell|python|docker)$")
    command: str
    args: str | None = None  # JSON array string
    cron_expr: str | None = None
    timezone: str = "Asia/Shanghai"
    enabled: bool = True
    timeout: int = Field(3600, ge=0)
    max_retries: int = Field(3, ge=0, le=10)
    retry_delay: int = Field(60, ge=0)
    working_dir: str | None = None
    env_vars: dict[str, Any] = {}
    plugin_id: int | None = None


class TaskCreate(TaskBase):
    pass


class TaskUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    task_type: str | None = None
    command: str | None = None
    args: str | None = None
    cron_expr: str | None = None
    timezone: str | None = None
    enabled: bool | None = None
    timeout: int | None = Field(None, ge=0)
    max_retries: int | None = Field(None, ge=0, le=10)
    retry_delay: int | None = Field(None, ge=0)
    working_dir: str | None = None
    env_vars: dict[str, Any] | None = None
    plugin_id: int | None = None


class TaskOut(TaskBase):
    id: int
    created_at: datetime
    updated_at: datetime
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None

    model_config = {"from_attributes": True}


class TaskExecutionOut(BaseModel):
    id: int
    task_id: int
    start_time: datetime
    end_time: datetime | None = None
    status: str
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    duration_ms: int | None = None
    triggered_by: str | None = None
    error_message: str | None = None

    model_config = {"from_attributes": True}


class TaskRunRequest(BaseModel):
    """Trigger an immediate run of a task."""
    pass

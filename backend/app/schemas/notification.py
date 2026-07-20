"""Notification schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class NotificationChannelCreate(BaseModel):
    name: str = Field(..., max_length=128)
    channel_type: str = Field(..., pattern="^(feishu|wechat_work|telegram|email)$")
    config: dict[str, Any] = {}
    enabled: bool = True
    is_default: bool = False


class NotificationChannelUpdate(BaseModel):
    name: str | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None
    is_default: bool | None = None


class NotificationChannelOut(BaseModel):
    id: int
    name: str
    channel_type: str
    config: dict[str, Any]
    enabled: bool
    is_default: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NotificationRecordOut(BaseModel):
    id: int
    channel_id: int | None = None
    channel_type: str
    title: str
    message: str
    level: str
    event_type: str | None = None
    status: str
    error_message: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class NotificationTestRequest(BaseModel):
    """Test notification channel."""
    channel_id: int
    title: str = "NASPilot Test"
    message: str = "This is a test notification from NASPilot."

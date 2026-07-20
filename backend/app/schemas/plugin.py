"""Plugin schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PluginOut(BaseModel):
    id: int
    slug: str
    name: str
    description: str | None = None
    version: str
    author: str | None = None
    homepage: str | None = None
    icon: str | None = None
    category: str
    entrypoint: str
    is_builtin: bool
    enabled: bool
    installed_at: datetime | None = None
    config_schema: dict[str, Any] | None = None

    model_config = {"from_attributes": True}


class PluginInstanceCreate(BaseModel):
    name: str = Field(..., max_length=128)
    config: dict[str, Any] = {}
    enabled: bool = True


class PluginInstanceUpdate(BaseModel):
    name: str | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class PluginInstanceOut(BaseModel):
    id: int
    plugin_id: int
    name: str
    config: dict[str, Any]
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

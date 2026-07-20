"""System endpoints — dashboard stats, logs, settings."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import CurrentUser
from app.models import LogEntry, Setting
from app.schemas.system import LogEntryOut, SettingOut, SettingUpdate, SystemStats
from app.services.system_service import get_system_stats

router = APIRouter(prefix="/system", tags=["system"])


# ── Dashboard ───────────────────────────────────────────────────────────


@router.get("/stats", response_model=SystemStats, summary="System stats")
async def stats(user: CurrentUser):
    """Return real-time CPU/memory/disk/docker/qB stats for the dashboard."""
    return get_system_stats()


# ── Logs ─────────────────────────────────────────────────────────────────


@router.get("/logs", response_model=list[LogEntryOut], summary="Query logs")
async def list_logs(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    level: str | None = None,
    source: str | None = None,
    search: str | None = None,
):
    q = select(LogEntry).order_by(LogEntry.id.desc())
    if level:
        q = q.where(LogEntry.level == level.upper())
    if source:
        q = q.where(LogEntry.source == source)
    if search:
        q = q.where(LogEntry.message.like(f"%{search}%"))
    q = q.limit(limit).offset(offset)
    result = await db.execute(q)
    return result.scalars().all()


# ── Settings ────────────────────────────────────────────────────────────


@router.get("/settings", response_model=list[SettingOut], summary="List settings")
async def list_settings(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    category: str | None = None,
):
    q = select(Setting)
    if category:
        q = q.where(Setting.category == category)
    result = await db.execute(q.order_by(Setting.key))
    return result.scalars().all()


@router.get("/settings/public", response_model=list[SettingOut], summary="Public settings")
async def public_settings(db: Annotated[AsyncSession, Depends(get_db)]):
    """Settings visible without auth (e.g. app name, version)."""
    result = await db.execute(select(Setting).where(Setting.is_public.is_(True)))
    return result.scalars().all()


@router.put("/settings/{key}", response_model=SettingOut, summary="Update setting")
async def update_setting(
    key: str,
    body: SettingUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    if not setting:
        raise HTTPException(status_code=404, detail="Setting not found")
    setting.value = body.value
    await db.commit()
    await db.refresh(setting)
    return setting

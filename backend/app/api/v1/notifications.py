"""Notification endpoints — channels + records + test."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import CurrentUser
from app.models import NotificationChannel, NotificationRecord
from app.schemas.notification import (
    NotificationChannelCreate,
    NotificationChannelOut,
    NotificationChannelUpdate,
    NotificationRecordOut,
    NotificationTestRequest,
)
from app.services.notification_service import send_notification, notify_default_channels

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/channels", response_model=list[NotificationChannelOut], summary="List channels")
async def list_channels(user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(select(NotificationChannel).order_by(NotificationChannel.id))
    return result.scalars().all()


@router.post("/channels", response_model=NotificationChannelOut, status_code=201, summary="Create channel")
async def create_channel(
    body: NotificationChannelCreate, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]
):
    ch = NotificationChannel(**body.model_dump())
    db.add(ch)
    await db.commit()
    await db.refresh(ch)
    return ch


@router.put("/channels/{channel_id}", response_model=NotificationChannelOut, summary="Update channel")
async def update_channel(
    channel_id: int,
    body: NotificationChannelUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(NotificationChannel).where(NotificationChannel.id == channel_id))
    ch = result.scalar_one_or_none()
    if not ch:
        raise HTTPException(status_code=404, detail="Channel not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(ch, k, v)
    await db.commit()
    await db.refresh(ch)
    return ch


@router.delete("/channels/{channel_id}", summary="Delete channel")
async def delete_channel(channel_id: int, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(select(NotificationChannel).where(NotificationChannel.id == channel_id))
    ch = result.scalar_one_or_none()
    if not ch:
        raise HTTPException(status_code=404, detail="Channel not found")
    ch_name = ch.name
    ch_type = ch.channel_type
    await db.delete(ch)
    await db.commit()
    # ── Notify other default channels ──
    await notify_default_channels(
        db,
        title="🗑️ 通知渠道已删除",
        message=f"通知渠道「{ch_name}」({ch_type}, ID:{channel_id}) 已被删除",
        level="warn",
        event_type="channel_deleted",
    )
    return {"message": "deleted"}


@router.post("/test", response_model=NotificationRecordOut, summary="Test notification")
async def test_notification(
    body: NotificationTestRequest, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]
):
    result = await db.execute(select(NotificationChannel).where(NotificationChannel.id == body.channel_id))
    ch = result.scalar_one_or_none()
    if not ch:
        raise HTTPException(status_code=404, detail="Channel not found")
    return await send_notification(db, ch, body.title, body.message, level="info", event_type="test")


@router.post("/channels/{channel_id}/test", response_model=NotificationRecordOut, summary="Test channel")
async def test_channel(
    channel_id: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(NotificationChannel).where(NotificationChannel.id == channel_id))
    ch = result.scalar_one_or_none()
    if not ch:
        raise HTTPException(status_code=404, detail="Channel not found")
    return await send_notification(db, ch, "NASPilot Test", "This is a test notification from NASPilot.", level="info", event_type="test")


@router.get("/records", response_model=list[NotificationRecordOut], summary="Notification history")
async def list_records(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    level: str | None = None,
):
    q = select(NotificationRecord).order_by(NotificationRecord.id.desc())
    if level:
        q = q.where(NotificationRecord.level == level)
    q = q.limit(limit).offset(offset)
    result = await db.execute(q)
    return result.scalars().all()

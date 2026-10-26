"""Media Subscription Center — YouTube subscription / task / history API.

Mounted at ``/api/v1/youtube`` (and mirrored at ``/api/youtube`` for
compatibility with the product specification).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import CurrentUser
from app.models import YoutubeDownloadHistory, YoutubeDownloadTask, YoutubeSubscription
from app.schemas.youtube import (
    DownloadHistoryOut,
    DownloadTaskOut,
    HistoryPage,
    SubscriptionCreate,
    SubscriptionOut,
    SubscriptionUpdate,
    SyncResult,
    YtdlpHealth,
)
from app.services.media import ytdlp
from app.services.media.job import YoutubeDownloadJob, enqueue_sync
from app.services.media.providers import get_provider_class, supported_types

logger = logging.getLogger("naspilot.youtube.api")

router = APIRouter(prefix="/youtube", tags=["youtube"])

# Keep strong references to background re-download tasks.
_REDOWNLOAD_TASKS: set[asyncio.Task] = set()


# ══════════════════════════════════════════════════════════════════════
#  Subscriptions
# ══════════════════════════════════════════════════════════════════════


async def _download_counts(db: AsyncSession) -> dict[int, int]:
    rows = await db.execute(
        select(YoutubeDownloadHistory.subscription_id, func.count(YoutubeDownloadHistory.id))
        .where(YoutubeDownloadHistory.status == "completed")
        .group_by(YoutubeDownloadHistory.subscription_id)
    )
    return {row[0]: int(row[1]) for row in rows.all() if row[0] is not None}


def _to_out(sub: YoutubeSubscription, count: int) -> SubscriptionOut:
    if not sub.enabled:
        status = "disabled"
    elif YoutubeDownloadJob.is_running(sub.id):
        status = "syncing"
    else:
        status = "idle"
    data = SubscriptionOut.model_validate(sub)
    data.download_count = count
    data.status = status
    return data


@router.get("/subscriptions", response_model=list[SubscriptionOut], summary="List subscriptions")
async def list_subscriptions(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    enabled: bool | None = None,
):
    q = select(YoutubeSubscription)
    if enabled is not None:
        q = q.where(YoutubeSubscription.enabled.is_(enabled))
    result = await db.execute(q.order_by(YoutubeSubscription.id))
    counts = await _download_counts(db)
    return [_to_out(sub, counts.get(sub.id, 0)) for sub in result.scalars().all()]


@router.post("/subscriptions", response_model=SubscriptionOut, status_code=201, summary="Create subscription")
async def create_subscription(
    body: SubscriptionCreate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        get_provider_class(body.type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    sub = YoutubeSubscription(**body.model_dump())
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    logger.info("YouTube subscription created: #%s %s (%s)", sub.id, sub.name, sub.type)
    return _to_out(sub, 0)


@router.get("/subscriptions/{sub_id}", response_model=SubscriptionOut, summary="Get subscription")
async def get_subscription(
    sub_id: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    sub = await db.get(YoutubeSubscription, sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    counts = await _download_counts(db)
    return _to_out(sub, counts.get(sub.id, 0))


@router.put("/subscriptions/{sub_id}", response_model=SubscriptionOut, summary="Update subscription")
async def update_subscription(
    sub_id: int,
    body: SubscriptionUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    sub = await db.get(YoutubeSubscription, sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="订阅不存在")

    payload = body.model_dump(exclude_unset=True)
    if payload.get("type"):
        try:
            get_provider_class(payload["type"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    for key, value in payload.items():
        setattr(sub, key, value)
    await db.commit()
    await db.refresh(sub)
    counts = await _download_counts(db)
    return _to_out(sub, counts.get(sub.id, 0))


@router.delete("/subscriptions/{sub_id}", summary="Delete subscription")
async def delete_subscription(
    sub_id: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    sub = await db.get(YoutubeSubscription, sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    await db.execute(
        update(YoutubeDownloadTask)
        .where(YoutubeDownloadTask.subscription_id == sub_id)
        .values(subscription_id=None)
    )
    # Keep the archive but detach it (portable across SQLite/MySQL/Postgres).
    await db.execute(
        update(YoutubeDownloadHistory)
        .where(YoutubeDownloadHistory.subscription_id == sub_id)
        .values(subscription_id=None)
    )
    await db.delete(sub)
    await db.commit()
    return {"message": "deleted", "id": sub_id}


@router.post("/subscriptions/{sub_id}/toggle", response_model=SubscriptionOut, summary="Enable / disable")
async def toggle_subscription(
    sub_id: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    sub = await db.get(YoutubeSubscription, sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    sub.enabled = not sub.enabled
    await db.commit()
    await db.refresh(sub)
    counts = await _download_counts(db)
    return _to_out(sub, counts.get(sub.id, 0))


# ══════════════════════════════════════════════════════════════════════
#  Sync
# ══════════════════════════════════════════════════════════════════════


@router.post("/sync/{sub_id}", response_model=SyncResult, summary="Sync subscription now")
async def sync_now(
    sub_id: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    sub = await db.get(YoutubeSubscription, sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    result = await enqueue_sync(sub_id)
    queued = result["status"] == "queued"
    return SyncResult(
        status=result["status"],
        subscription_id=sub_id,
        message="已加入同步队列" if queued else "该订阅正在同步中",
    )


@router.post("/sync-all", response_model=list[SyncResult], summary="Sync every enabled subscription")
async def sync_all(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(YoutubeSubscription.id).where(YoutubeSubscription.enabled.is_(True))
    )
    out: list[SyncResult] = []
    for (sub_id,) in result.all():
        status = (await enqueue_sync(sub_id))["status"]
        out.append(SyncResult(status=status, subscription_id=sub_id))
    return out


# ══════════════════════════════════════════════════════════════════════
#  Tasks (live queue)
# ══════════════════════════════════════════════════════════════════════


@router.get("/tasks", response_model=list[DownloadTaskOut], summary="Download tasks")
async def list_tasks(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str | None = Query(None, description="pending|downloading|processing|completed|failed"),
    active_only: bool = False,
    limit: int = Query(200, ge=1, le=1000),
):
    q = select(YoutubeDownloadTask)
    if status:
        q = q.where(YoutubeDownloadTask.status == status)
    elif active_only:
        q = q.where(YoutubeDownloadTask.status.in_(("pending", "downloading", "processing")))
    result = await db.execute(q.order_by(YoutubeDownloadTask.id.desc()).limit(limit))
    return result.scalars().all()


@router.delete("/tasks", summary="Clear finished tasks")
async def clear_tasks(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from sqlalchemy import delete

    result = await db.execute(
        delete(YoutubeDownloadTask).where(
            YoutubeDownloadTask.status.in_(("completed", "failed"))
        )
    )
    await db.commit()
    return {"deleted": int(result.rowcount or 0)}


# ══════════════════════════════════════════════════════════════════════
#  History
# ══════════════════════════════════════════════════════════════════════


@router.get("/history", response_model=HistoryPage, summary="Download history")
async def list_history(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    search: str | None = None,
    status: str | None = None,
    subscription_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    conditions = []
    if search:
        like = f"%{search.strip()}%"
        conditions.append(
            or_(
                YoutubeDownloadHistory.title.ilike(like),
                YoutubeDownloadHistory.channel.ilike(like),
                YoutubeDownloadHistory.playlist.ilike(like),
                YoutubeDownloadHistory.video_id.ilike(like),
            )
        )
    if status:
        conditions.append(YoutubeDownloadHistory.status == status)
    if subscription_id:
        conditions.append(YoutubeDownloadHistory.subscription_id == subscription_id)

    count_q = select(func.count(YoutubeDownloadHistory.id))
    list_q = select(YoutubeDownloadHistory)
    for cond in conditions:
        count_q = count_q.where(cond)
        list_q = list_q.where(cond)

    total = int(await db.scalar(count_q) or 0)
    result = await db.execute(
        list_q.order_by(YoutubeDownloadHistory.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [DownloadHistoryOut.model_validate(row) for row in result.scalars().all()]
    return HistoryPage(items=items, total=total, page=page, page_size=page_size)


@router.delete("/history/{history_id}", summary="Delete history record")
async def delete_history(
    history_id: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    delete_file: bool = False,
):
    row = await db.get(YoutubeDownloadHistory, history_id)
    if row is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    removed_file = False
    if delete_file and row.file_path:
        try:
            if os.path.isfile(row.file_path):
                os.remove(row.file_path)
                removed_file = True
        except OSError as exc:
            logger.warning("Failed to delete media file %s: %s", row.file_path, exc)
    await db.delete(row)
    await db.commit()
    return {"message": "deleted", "id": history_id, "removed_file": removed_file}


@router.post("/history/{history_id}/redownload", summary="Re-download a history item")
async def redownload_history(
    history_id: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    row = await db.get(YoutubeDownloadHistory, history_id)
    if row is None:
        raise HTTPException(status_code=404, detail="记录不存在")

    async def _run() -> None:
        try:
            result = await YoutubeDownloadJob().redownload(history_id)
            logger.info("Redownload %s finished: %s", history_id, result.get("status"))
        except Exception:
            logger.exception("Redownload %s failed", history_id)

    # Fire-and-forget: the UI watches the task queue for live progress.
    bg = asyncio.create_task(_run())
    _REDOWNLOAD_TASKS.add(bg)
    bg.add_done_callback(_REDOWNLOAD_TASKS.discard)
    return {"status": "queued", "id": history_id}


# ══════════════════════════════════════════════════════════════════════
#  Meta / health
# ══════════════════════════════════════════════════════════════════════


@router.get("/types", summary="Supported subscription providers")
async def list_types(user: CurrentUser):
    return supported_types()


@router.get("/health", response_model=YtdlpHealth, summary="yt-dlp / ffmpeg status")
async def youtube_health(user: CurrentUser):
    return YtdlpHealth(**await ytdlp.dependency_health())


@router.get("/net-test", summary="Network / proxy reachability test")
async def youtube_net_test(user: CurrentUser):
    """Probe YouTube API + video CDN reachability (uses the configured proxy)."""
    return await ytdlp.check_network()

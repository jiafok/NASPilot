"""Media Download Center API — `/api/v1/media/*` (+ `/api/media/*` alias).

Endpoints
---------
subscriptions  GET/POST/PUT/DELETE + toggle
sync           POST /sync/{id}     立即同步
sync-all       POST /sync-all
tasks          GET (实时进度) / POST {pause,resume,cancel,retry} / DELETE
history        GET (搜索分页) / DELETE / POST /redownload
providers      GET  可用 Provider
detect         GET  URL 识别（新增订阅时自动猜来源/类型）
mediaservers   GET  Emby/Jellyfin/Audiobookshelf 集成信息
"""

from __future__ import annotations

import logging
import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import CurrentUser
from app.models import (
    MediaDownloadHistory,
    MediaDownloadTask,
    MediaSubscription,
)
from app.schemas.media_center import (
    HistoryOut,
    HistoryPage,
    MediaHealth,
    MediaServerOut,
    ProviderInfo,
    SubscriptionCreate,
    SubscriptionOut,
    SubscriptionUpdate,
    SyncResult,
    TaskOut,
    UrlDetectOut,
)
from app.services.media_center import engine, mediaserver, queue
from app.services.media_center.base import all_providers, detect_provider, get_provider_class
from app.services.media_center.job import MediaSyncJob
from app.services.media_center.paths import suggest_library_paths
from app.services.media_center.providers import provider_infos

logger = logging.getLogger("naspilot.media.api")

router = APIRouter(prefix="/media", tags=["media"])

_TASK_ACTIVE = ("pending", "downloading", "processing")


# ══════════════════════════════════════════════════════════════════════
#  Subscriptions
# ══════════════════════════════════════════════════════════════════════


async def _counts(db: AsyncSession) -> dict[int, int]:
    rows = await db.execute(
        select(MediaDownloadHistory.subscription_id, func.count(MediaDownloadHistory.id))
        .where(MediaDownloadHistory.status == "completed")
        .group_by(MediaDownloadHistory.subscription_id)
    )
    return {r[0]: int(r[1]) for r in rows.all() if r[0] is not None}


def _to_out(sub: MediaSubscription, count: int) -> SubscriptionOut:
    if not sub.enabled:
        state = "disabled"
    elif MediaSyncJob.is_running(sub.id):
        state = "syncing"
    elif sub.last_error:
        state = "error"
    else:
        state = "idle"
    out = SubscriptionOut.model_validate(sub)
    out.download_count = count
    out.status = state
    out.proxy_url = sub.proxy_url
    return out


@router.get("/subscriptions", response_model=list[SubscriptionOut], summary="List subscriptions")
async def list_subscriptions(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    provider: str | None = None,
    enabled: bool | None = None,
):
    q = select(MediaSubscription)
    if provider:
        q = q.where(MediaSubscription.provider == provider)
    if enabled is not None:
        q = q.where(MediaSubscription.enabled.is_(enabled))
    result = await db.execute(q.order_by(MediaSubscription.id))
    counts = await _counts(db)
    return [_to_out(sub, counts.get(sub.id, 0)) for sub in result.scalars().all()]


@router.post("/subscriptions", response_model=SubscriptionOut, status_code=201, summary="Create subscription")
async def create_subscription(
    body: SubscriptionCreate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    cls = get_provider_class(body.provider)
    if cls is None:
        raise HTTPException(status_code=400, detail=f"未知来源: {body.provider}")
    if not cls.implemented:
        raise HTTPException(status_code=400, detail=f"{cls.display_name} 尚未实现（预留）")
    ok, msg = cls.validate_url(body.url)
    if not ok:
        raise HTTPException(status_code=400, detail=msg or "URL 无效")

    sub = MediaSubscription(**body.model_dump())
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    from app.services.media_center.scheduler import upsert_subscription_schedule

    upsert_subscription_schedule(sub)
    logger.info("media subscription created: #%s %s [%s]", sub.id, sub.name, sub.provider)
    return _to_out(sub, 0)


@router.get("/subscriptions/{sub_id}", response_model=SubscriptionOut, summary="Get subscription")
async def get_subscription(
    sub_id: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    sub = await db.get(MediaSubscription, sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    counts = await _counts(db)
    return _to_out(sub, counts.get(sub.id, 0))


@router.put("/subscriptions/{sub_id}", response_model=SubscriptionOut, summary="Update subscription")
async def update_subscription(
    sub_id: int,
    body: SubscriptionUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    sub = await db.get(MediaSubscription, sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="订阅不存在")

    payload = body.model_dump(exclude_unset=True)
    for key in ("proxy_password",):
        if key in payload and payload[key] == "":
            payload[key] = None

    if payload.get("provider"):
        cls = get_provider_class(payload["provider"])
        if cls is None:
            raise HTTPException(status_code=400, detail=f"未知来源: {payload['provider']}")
        if not cls.implemented:
            raise HTTPException(status_code=400, detail=f"{cls.display_name} 尚未实现（预留）")
    if payload.get("url"):
        cls = get_provider_class(payload.get("provider") or sub.provider)
        if cls:
            ok, msg = cls.validate_url(payload["url"])
            if not ok:
                raise HTTPException(status_code=400, detail=msg or "URL 无效")

    for key, value in payload.items():
        setattr(sub, key, value)
    await db.commit()
    await db.refresh(sub)

    from app.services.media_center.scheduler import upsert_subscription_schedule

    upsert_subscription_schedule(sub)
    counts = await _counts(db)
    return _to_out(sub, counts.get(sub.id, 0))


@router.delete("/subscriptions/{sub_id}", summary="Delete subscription")
async def delete_subscription(
    sub_id: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    sub = await db.get(MediaSubscription, sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="订阅不存在")

    # Keep the archive (detach), drop the live tasks.
    await db.execute(
        update(MediaDownloadHistory)
        .where(MediaDownloadHistory.subscription_id == sub_id)
        .values(subscription_id=None)
    )
    await db.delete(sub)
    await db.commit()

    from app.services.media_center.scheduler import remove_subscription_schedule

    remove_subscription_schedule(sub_id)
    return {"message": "deleted", "id": sub_id}


@router.post("/subscriptions/{sub_id}/toggle", response_model=SubscriptionOut, summary="Enable / disable")
async def toggle_subscription(
    sub_id: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    sub = await db.get(MediaSubscription, sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    sub.enabled = not sub.enabled
    await db.commit()
    await db.refresh(sub)

    from app.services.media_center.scheduler import upsert_subscription_schedule

    upsert_subscription_schedule(sub)
    counts = await _counts(db)
    return _to_out(sub, counts.get(sub.id, 0))


# ══════════════════════════════════════════════════════════════════════
#  Sync
# ══════════════════════════════════════════════════════════════════════


@router.post("/sync/{sub_id}", response_model=SyncResult, summary="Sync now")
async def sync_now(
    sub_id: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    sub = await db.get(MediaSubscription, sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="订阅不存在")

    import asyncio

    task = asyncio.create_task(MediaSyncJob().sync_subscription(sub_id, manual=True))
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return SyncResult(status="queued", subscription_id=sub_id, message="已开始同步")


@router.post("/sync-all", response_model=list[SyncResult], summary="Sync every enabled subscription")
async def sync_all(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    rows = await db.execute(
        select(MediaSubscription.id).where(MediaSubscription.enabled.is_(True))
    )
    import asyncio

    out: list[SyncResult] = []
    for (sub_id,) in rows.all():
        if MediaSyncJob.is_running(sub_id):
            out.append(SyncResult(status="already_running", subscription_id=sub_id))
            continue
        task = asyncio.create_task(MediaSyncJob().sync_subscription(sub_id, manual=True))
        _BACKGROUND.add(task)
        task.add_done_callback(_BACKGROUND.discard)
        out.append(SyncResult(status="queued", subscription_id=sub_id))
    return out


_BACKGROUND: set = set()


# ══════════════════════════════════════════════════════════════════════
#  Tasks
# ══════════════════════════════════════════════════════════════════════


@router.get("/tasks", response_model=list[TaskOut], summary="Download tasks")
async def list_tasks(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str | None = Query(None, description="pending|downloading|paused|processing|completed|failed|cancelled"),
    active_only: bool = False,
    provider: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
):
    q = select(MediaDownloadTask)
    if status:
        q = q.where(MediaDownloadTask.status == status)
    elif active_only:
        q = q.where(
            MediaDownloadTask.status.in_(
                ("pending", "downloading", "processing", "paused", "pausing", "cancelling")
            )
        )
    if provider:
        q = q.where(MediaDownloadTask.provider == provider)
    result = await db.execute(q.order_by(MediaDownloadTask.id.desc()).limit(limit))
    return result.scalars().all()


@router.post("/tasks/{task_id}/{action}", summary="Task control (pause/resume/cancel/retry)")
async def task_control(
    task_id: int,
    action: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if action not in ("pause", "resume", "cancel", "retry"):
        raise HTTPException(status_code=400, detail="action 必须是 pause/resume/cancel/retry")
    row = await db.get(MediaDownloadTask, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    handlers = {
        "pause": queue.pause_task,
        "resume": queue.resume_task,
        "cancel": queue.cancel_task,
        "retry": queue.retry_task,
    }
    return await handlers[action](task_id)


@router.delete("/tasks", summary="Clear finished tasks")
async def clear_tasks(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from sqlalchemy import delete

    result = await db.execute(
        delete(MediaDownloadTask).where(
            MediaDownloadTask.status.in_(("completed", "failed", "cancelled"))
        )
    )
    await db.commit()
    return {"deleted": int(result.rowcount or 0)}


@router.get("/tasks/stats", summary="Queue statistics")
async def task_stats(user: CurrentUser):
    return await queue.stats()


# ══════════════════════════════════════════════════════════════════════
#  History
# ══════════════════════════════════════════════════════════════════════


@router.get("/history", response_model=HistoryPage, summary="Download history")
async def list_history(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    search: str | None = None,
    provider: str | None = None,
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
                MediaDownloadHistory.title.ilike(like),
                MediaDownloadHistory.channel_name.ilike(like),
                MediaDownloadHistory.playlist_name.ilike(like),
                MediaDownloadHistory.media_id.ilike(like),
            )
        )
    if provider:
        conditions.append(MediaDownloadHistory.provider == provider)
    if status:
        conditions.append(MediaDownloadHistory.status == status)
    if subscription_id:
        conditions.append(MediaDownloadHistory.subscription_id == subscription_id)

    count_q = select(func.count(MediaDownloadHistory.id))
    list_q = select(MediaDownloadHistory)
    for cond in conditions:
        count_q = count_q.where(cond)
        list_q = list_q.where(cond)

    total = int(await db.scalar(count_q) or 0)
    rows = await db.execute(
        list_q.order_by(MediaDownloadHistory.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items: list[HistoryOut] = []
    for row in rows.scalars().all():
        item = HistoryOut.model_validate(row)
        item.media_type = row.media_type
        items.append(item)
    return HistoryPage(items=items, total=total, page=page, page_size=page_size)


@router.delete("/history/{history_id}", summary="Delete history record")
async def delete_history(
    history_id: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    delete_file: bool = False,
):
    row = await db.get(MediaDownloadHistory, history_id)
    if row is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    removed = False
    if delete_file and row.file_path:
        try:
            if os.path.isfile(row.file_path):
                os.remove(row.file_path)
                removed = True
        except OSError as exc:
            logger.warning("failed to delete media file %s: %s", row.file_path, exc)
    await db.delete(row)
    await db.commit()
    return {"message": "deleted", "id": history_id, "removed_file": removed}


@router.post("/history/{history_id}/redownload", summary="Re-download history item")
async def redownload(
    history_id: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    row = await db.get(MediaDownloadHistory, history_id)
    if row is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    result = await MediaSyncJob().redownload(history_id)
    if result["status"] == "no_subscription":
        raise HTTPException(status_code=400, detail=result.get("message", "原订阅已删除"))
    return result


@router.get("/history/{history_id}/links", summary="Media server deep links for an item")
async def history_links(
    history_id: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    row = await db.get(MediaDownloadHistory, history_id)
    if row is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"links": mediaserver.links_for_path(row.file_path)}


# ══════════════════════════════════════════════════════════════════════
#  Meta
# ══════════════════════════════════════════════════════════════════════


@router.get("/providers", response_model=list[ProviderInfo], summary="Available providers")
async def list_providers(user: CurrentUser):
    return provider_infos()


@router.get("/detect", response_model=UrlDetectOut, summary="Detect provider + type from URL")
async def detect(url: str = Query(..., min_length=4), user: CurrentUser = None):  # type: ignore[assignment]
    provider = detect_provider(url)
    cls = get_provider_class(provider)
    if cls is None:
        return UrlDetectOut(provider="custom", type="video", valid=True)
    ok, msg = cls.validate_url(url)
    return UrlDetectOut(
        provider=provider,
        type=cls.guess_type(url),
        valid=ok,
        message=msg,
    )


@router.get("/mediaservers", response_model=list[MediaServerOut], summary="Media server integration")
async def media_servers(user: CurrentUser):
    return mediaserver.describe()


@router.get("/directories", summary="Suggested library directories")
async def directories(user: CurrentUser):
    return {
        "video": str(settings.MEDIA_VIDEO_DIR),
        "audio": str(settings.MEDIA_AUDIO_DIR),
        "library_paths": suggest_library_paths(),
    }


@router.get("/network-test", summary="Network / proxy reachability test")
async def network_test(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    provider: str | None = Query(None, description="只检测指定来源"),
    subscription_id: int | None = Query(None, description="使用该订阅的代理策略检测"),
):
    """Reachability for one provider (with its proxy policy) or all providers."""
    if subscription_id is not None:
        sub = await db.get(MediaSubscription, subscription_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="订阅不存在")
        from app.services.media_center.base import resolve_options

        opts = resolve_options(sub)
        return await engine.check_provider(sub.provider, url=sub.url, proxy=opts.proxy)
    if provider:
        return await engine.check_provider(provider)
    return await engine.check_all_providers()


# ══════════════════════════════════════════════════════════════════════
#  Health  (also exposed as the top-level ``/health/media``)
# ══════════════════════════════════════════════════════════════════════


async def build_health() -> dict:
    yt_version, ff_version = await engine.dependency_versions()
    yt_ok = engine.ytdlp_installed()
    ff_ok = engine.ffmpeg_installed()

    if yt_ok and ff_ok:
        status, detail = "ok", None
    elif yt_ok:
        status, detail = "degraded", "ffmpeg 未安装：无法合并音视频 / 转换字幕封面 / 提取音频"
    else:
        status, detail = "error", "yt-dlp 未安装：无法下载任何媒体源"

    network = await engine.check_all_providers()
    st = await queue.stats()

    return {
        "ytdlp_installed": yt_ok,
        "ytdlp_version": yt_version,
        "ffmpeg_installed": ff_ok,
        "ffmpeg_version": ff_version,
        "status": status,
        "detail": detail,
        "providers": provider_infos(),
        "network": network,
        "directories": {
            "video": str(settings.MEDIA_VIDEO_DIR),
            "audio": str(settings.MEDIA_AUDIO_DIR),
        },
        "active_downloads": st["active"],
        "queued_tasks": st["counts"].get("pending", 0),
    }


@router.get("/health", response_model=MediaHealth, summary="Media stack health")
async def media_health(user: CurrentUser):
    return MediaHealth(**await build_health())


# expose the provider list for the UI dropdown without auth noise
@router.get("/providers/simple", summary="Provider options for select boxes")
async def provider_options(user: CurrentUser):
    return [
        {
            "value": name,
            "label": cls.display_name,
            "implemented": cls.implemented,
            "types": list(cls.supported_types),
        }
        for name, cls in sorted(all_providers().items())
    ]

"""YoutubeDownloadJob — incremental download engine for media subscriptions.

Flow (per subscription)
-----------------------
1. enumerate the channel / playlist with yt-dlp
2. load the *video ids* already archived in ``youtube_download_history``
3. filter them out (never by file name)
4. download the remaining items one by one, streaming progress into
   ``youtube_download_tasks``
5. archive every success in ``youtube_download_history``
6. update ``last_check_time``

Runs every ``YOUTUBE_SYNC_INTERVAL_HOURS`` hours via APScheduler and can be
triggered on demand through ``POST /api/v1/youtube/sync/{id}``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import async_session_factory
from app.models import YoutubeDownloadHistory, YoutubeDownloadTask, YoutubeSubscription
from app.services.media.base import DownloadResult, MediaItem
from app.services.media.providers import build_provider

logger = logging.getLogger("naspilot.youtube.job")


@dataclass
class _TransientSubscription:
    """Fallback when a history row outlived its subscription."""

    id: int | None = None
    name: str = "YouTube"
    url: str = ""
    type: str = "channel"
    enabled: bool = True
    audio_only: bool = False
    media_mode: str = "video"
    video_height: int = 0
    audio_format: str = ""
    download_path: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


class YoutubeDownloadJob:
    """Stateless-ish job runner; keeps a per-subscription lock in memory."""

    _locks: dict[int, asyncio.Lock] = {}
    _running: set[int] = set()

    # ── introspection (used by the API for the "syncing" badge) ────────
    @classmethod
    def is_running(cls, subscription_id: int) -> bool:
        return subscription_id in cls._running

    @classmethod
    def running_ids(cls) -> set[int]:
        return set(cls._running)

    # ── public entry point ─────────────────────────────────────────────
    async def sync_subscription(
        self,
        subscription_id: int,
        *,
        manual: bool = False,
        notify: bool = True,
    ) -> dict:
        """Check one subscription and download everything new."""
        lock = self._locks.setdefault(subscription_id, asyncio.Lock())
        if lock.locked():
            return {"status": "already_running", "subscription_id": subscription_id}

        async with lock:
            self._running.add(subscription_id)
            try:
                return await self._sync(subscription_id, manual=manual, notify=notify)
            except Exception as exc:
                logger.exception("YouTube sync failed for subscription %s", subscription_id)
                return {
                    "status": "error",
                    "subscription_id": subscription_id,
                    "error": str(exc)[:500],
                }
            finally:
                self._running.discard(subscription_id)

    async def _sync(self, subscription_id: int, *, manual: bool, notify: bool) -> dict:
        async with async_session_factory() as db:
            sub = await db.get(YoutubeSubscription, subscription_id)
            if sub is None:
                return {"status": "not_found", "subscription_id": subscription_id}
            if not sub.enabled and not manual:
                return {"status": "disabled", "subscription_id": subscription_id}
            if (sub.type or "").lower() not in ("channel", "playlist", "youtube"):
                # Handled by the Media Download Center instead.
                logger.debug(
                    "skipping non-YouTube row #%s (type=%s) — use /api/media/subscriptions",
                    subscription_id, sub.type,
                )
                return {"status": "unsupported", "subscription_id": subscription_id,
                        "error": "该订阅不属于 YouTube 模块，请在「媒体中心 → 媒体订阅」中管理"}
            snapshot = _TransientSubscription(
                id=sub.id,
                name=sub.name,
                url=sub.url,
                type=sub.type,
                enabled=sub.enabled,
                audio_only=sub.audio_only,
                media_mode=sub.media_mode,
                video_height=sub.video_height,
                audio_format=sub.audio_format,
                download_path=sub.download_path,
            )

        provider = build_provider(snapshot)

        # 1. enumerate
        try:
            items = await provider.list_items()
        except NotImplementedError as exc:
            logger.info("Provider not available for subscription %s: %s", subscription_id, exc)
            return {"status": "unsupported", "subscription_id": subscription_id, "error": str(exc)}
        except Exception as exc:
            logger.warning("Enumeration failed for %s: %s", subscription_id, exc)
            return {"status": "error", "subscription_id": subscription_id, "error": str(exc)[:500]}

        # 2. known video ids (global — video_id is globally unique)
        async with async_session_factory() as db:
            known = await _completed_video_ids(db)

        # 3+4. incremental filter, hard cap per sync to protect the disk
        new_items = [item for item in items if item.item_id not in known]
        limit = max(1, int(settings.YOUTUBE_MAX_PER_SYNC or 20))
        new_items = new_items[:limit]

        downloaded: list[DownloadResult] = []
        for item in new_items:
            task_id = await self._create_task(snapshot, item)
            result = await self._download_and_record(snapshot, item, task_id)
            downloaded.append(result)

        # 5. update last check time
        async with async_session_factory() as db:
            await db.execute(
                update(YoutubeSubscription)
                .where(YoutubeSubscription.id == subscription_id)
                .values(last_check_time=_now(), updated_time=_now())
            )
            await db.commit()

        ok = [r for r in downloaded if r.status == "completed"]
        failed = [r for r in downloaded if r.status != "completed"]
        logger.info(
            "YouTube sync #%s done: scanned=%d new=%d ok=%d failed=%d",
            subscription_id,
            len(items),
            len(new_items),
            len(ok),
            len(failed),
        )

        if notify and ok:
            await self._notify_success(snapshot, ok)

        return {
            "status": "ok",
            "subscription_id": subscription_id,
            "scanned": len(items),
            "new": len(new_items),
            "downloaded": len(ok),
            "failed": len(failed),
        }

    # ── single item ────────────────────────────────────────────────────
    async def _create_task(self, sub: _TransientSubscription, item: MediaItem) -> int:
        async with async_session_factory() as db:
            row = YoutubeDownloadTask(
                subscription_id=sub.id,
                subscription_name=sub.name,
                video_id=item.item_id,
                title=item.title,
                status="pending",
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row.id

    async def _download_and_record(
        self,
        sub: _TransientSubscription,
        item: MediaItem,
        task_id: int,
    ) -> DownloadResult:
        provider = build_provider(sub)

        async def on_progress(
            percent: float,
            speed: str | None,
            downloaded: int,
            total: int,
            eta: str | None,
            status: str,
        ) -> None:
            await self._update_task(
                task_id,
                status=status,
                progress=round(percent, 1),
                speed=speed,
                downloaded_bytes=downloaded,
                total_bytes=total,
                eta=eta,
            )

        await self._update_task(task_id, status="downloading")
        try:
            result = await provider.download(item, on_progress=on_progress)
        except Exception as exc:  # defensive: provider already returns failures
            logger.exception("Download crashed for %s", item.item_id)
            result = DownloadResult(item=item, status="failed", error=str(exc)[:800])

        async with async_session_factory() as db:
            await _upsert_history(db, sub, item, result)
            await db.execute(
                update(YoutubeDownloadTask)
                .where(YoutubeDownloadTask.id == task_id)
                .values(
                    status="completed" if result.status == "completed" else "failed",
                    progress=100.0 if result.status == "completed" else 0.0,
                    error_message=result.error,
                    updated_time=_now(),
                )
            )
            await db.commit()
        return result

    async def _update_task(self, task_id: int, **fields) -> None:
        try:
            async with async_session_factory() as db:
                await db.execute(
                    update(YoutubeDownloadTask)
                    .where(YoutubeDownloadTask.id == task_id)
                    .values(updated_time=_now(), **fields)
                )
                await db.commit()
        except Exception:
            logger.debug("Failed to update download task %s", task_id, exc_info=True)

    # ── manual re-download (history page) ──────────────────────────────
    async def redownload(self, history_id: int) -> dict:
        async with async_session_factory() as db:
            hist = await db.get(YoutubeDownloadHistory, history_id)
            if hist is None:
                return {"status": "not_found"}
            sub: _TransientSubscription
            if hist.subscription_id:
                row = await db.get(YoutubeSubscription, hist.subscription_id)
                sub = _TransientSubscription(
                    id=row.id,
                    name=row.name,
                    url=row.url,
                    type=row.type,
                    enabled=row.enabled,
                    audio_only=row.audio_only,
                    media_mode=row.media_mode,
                    video_height=row.video_height,
                    audio_format=row.audio_format,
                    download_path=row.download_path,
                ) if row else _TransientSubscription()
            else:
                hist_mode = hist.media_type if hist.media_type in ("video", "audio", "both") else "video"
                sub = _TransientSubscription(
                    audio_only=(hist_mode == "audio"),
                    media_mode=hist_mode,
                )
            if sub.name == "YouTube" and hist.channel:
                sub.name = hist.channel

            item = MediaItem(
                item_id=hist.video_id,
                title=hist.title or hist.video_id,
                url=f"https://www.youtube.com/watch?v={hist.video_id}",
                channel=hist.channel,
                playlist=hist.playlist,
                publish_date=hist.publish_date,
            )
            # Remove the old archive row so the unique index allows a fresh one.
            await db.delete(hist)
            await db.commit()

        task_id = await self._create_task(sub, item)
        result = await self._download_and_record(sub, item, task_id)
        return {
            "status": result.status,
            "video_id": item.item_id,
            "file_path": result.file_path,
            "error": result.error,
        }

    # ── notifications ─────────────────────────────────────────────────
    async def _notify_success(self, sub: _TransientSubscription, results: list[DownloadResult]) -> None:
        try:
            from app.services.notification_service import notify_default_channels

            titles = "\n".join(f"• {r.title or r.item.item_id}" for r in results[:10])
            extra = f"\n… 共 {len(results)} 个" if len(results) > 10 else ""
            async with async_session_factory() as db:
                await notify_default_channels(
                    db,
                    title=f"📺 已下载 {len(results)} 个新视频",
                    message=f"订阅「{sub.name}」新增内容：\n{titles}{extra}",
                    level="info",
                    event_type="youtube_downloaded",
                )
        except Exception:
            logger.debug("YouTube success notification failed", exc_info=True)


# ══════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════


async def _completed_video_ids(db: AsyncSession) -> set[str]:
    """Video ids already successfully archived — the ONLY de-dup source."""
    result = await db.execute(
        select(YoutubeDownloadHistory.video_id).where(YoutubeDownloadHistory.status == "completed")
    )
    return {row[0] for row in result.all() if row[0]}


async def _upsert_history(
    db: AsyncSession,
    sub: _TransientSubscription,
    item: MediaItem,
    result: DownloadResult,
) -> None:
    result_row = await db.execute(
        select(YoutubeDownloadHistory).where(YoutubeDownloadHistory.video_id == item.item_id)
    )
    row = result_row.scalar_one_or_none()
    payload = {
        "title": result.title or item.title,
        "channel": item.channel,
        "playlist": item.playlist,
        "publish_date": item.publish_date,
        "download_time": _now() if result.status == "completed" else None,
        "file_path": result.file_path,
        "file_size": result.file_size,
        "status": result.status,
        "media_type": result.media_type,
        "subscription_id": sub.id,
    }
    if row is None:
        db.add(YoutubeDownloadHistory(video_id=item.item_id, **payload))
    else:
        for key, value in payload.items():
            setattr(row, key, value)


# ══════════════════════════════════════════════════════════════════════
#  Scheduler / bulk entry points
# ══════════════════════════════════════════════════════════════════════


async def run_all_subscriptions() -> dict:
    """Scheduled 6-hour sweep over every enabled subscription."""
    async with async_session_factory() as db:
        result = await db.execute(
            select(YoutubeSubscription.id).where(YoutubeSubscription.enabled.is_(True))
        )
        ids = [row[0] for row in result.all()]

    logger.info("YouTube scheduled sweep — %d enabled subscription(s)", len(ids))
    job = YoutubeDownloadJob()
    summary = {"total": len(ids), "downloaded": 0, "failed": 0, "errors": 0}
    for sub_id in ids:
        outcome = await job.sync_subscription(sub_id, notify=False)
        status = outcome.get("status")
        if status == "ok":
            summary["downloaded"] += int(outcome.get("downloaded", 0))
            summary["failed"] += int(outcome.get("failed", 0))
        elif status not in ("disabled", "already_running"):
            summary["errors"] += 1

    # Housekeeping: drop stale finished task rows.
    try:
        await cleanup_finished_tasks(int(settings.YOUTUBE_RETENTION_DAYS or 30))
    except Exception:
        logger.debug("Task cleanup failed", exc_info=True)
    logger.info("YouTube scheduled sweep finished: %s", summary)
    return summary


async def cleanup_finished_tasks(days: int = 30) -> int:
    if days <= 0:
        return 0
    cutoff = _now() - timedelta(days=days)
    async with async_session_factory() as db:
        result = await db.execute(
            delete(YoutubeDownloadTask).where(
                YoutubeDownloadTask.status.in_(("completed", "failed")),
                YoutubeDownloadTask.updated_time < cutoff,
            )
        )
        await db.commit()
        return int(result.rowcount or 0)


async def enqueue_sync(subscription_id: int) -> dict:
    """Fire-and-forget manual sync (used by the REST endpoint)."""
    job = YoutubeDownloadJob()
    if YoutubeDownloadJob.is_running(subscription_id):
        return {"status": "already_running", "subscription_id": subscription_id}

    task = asyncio.create_task(job.sync_subscription(subscription_id, manual=True))
    # Keep a strong reference so the task is not garbage collected.
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return {"status": "queued", "subscription_id": subscription_id}


_BACKGROUND_TASKS: set[asyncio.Task] = set()

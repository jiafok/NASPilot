"""Sync job — discover new items and queue them for download."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update

from app.core.config import settings
from app.core.database import async_session_factory
from app.models import (
    MediaDownloadHistory,
    MediaDownloadTask,
    MediaSubscription,
)
from app.services.media_center.base import MediaItem, build_provider, resolve_options
from app.services.media_center.queue import manager

logger = logging.getLogger("naspilot.media.job")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MediaSyncJob:
    """Discover → de-duplicate → enqueue. One lock per subscription."""

    _locks: dict[int, asyncio.Lock] = {}
    _running: set[int] = set()

    @classmethod
    def is_running(cls, subscription_id: int) -> bool:
        return subscription_id in cls._running

    @classmethod
    def running_ids(cls) -> set[int]:
        return set(cls._running)

    # ── public ────────────────────────────────────────────────────────
    async def sync_subscription(self, subscription_id: int, *, manual: bool = False) -> dict:
        lock = self._locks.setdefault(subscription_id, asyncio.Lock())
        if lock.locked():
            return {"status": "already_running", "subscription_id": subscription_id}

        async with lock:
            self._running.add(subscription_id)
            try:
                return await self._sync(subscription_id, manual=manual)
            except Exception as exc:
                logger.exception("media sync failed for subscription %s", subscription_id)
                await self._record_error(subscription_id, str(exc))
                return {"status": "error", "subscription_id": subscription_id, "error": str(exc)[:500]}
            finally:
                self._running.discard(subscription_id)

    async def _sync(self, subscription_id: int, *, manual: bool) -> dict:
        async with async_session_factory() as db:
            sub = await db.get(MediaSubscription, subscription_id)
            if sub is None:
                return {"status": "not_found", "subscription_id": subscription_id}
            if not sub.enabled and not manual:
                return {"status": "disabled", "subscription_id": subscription_id}
            db.expunge(sub)

        provider = build_provider(sub)

        try:
            items = await provider.list_items()
        except NotImplementedError as exc:
            await self._record_error(subscription_id, str(exc))
            return {"status": "unsupported", "subscription_id": subscription_id, "error": str(exc)}
        except Exception as exc:
            await self._record_error(subscription_id, str(exc))
            logger.warning(
                "media sync #%s (%s) failed: %s", subscription_id, sub.provider, exc
            )
            return {"status": "error", "subscription_id": subscription_id, "error": str(exc)[:500]}

        known = await self._known_ids(sub.provider)
        new_items = [i for i in items if i.media_id not in known]
        cap = max(1, int(sub.max_items_per_sync or settings.MEDIA_MAX_PER_SYNC or 20))
        new_items = new_items[:cap]

        queued: list[int] = []
        for item in new_items:
            task_id = await self._create_task(sub, item)
            if task_id:
                queued.append(task_id)
                manager.submit(task_id)

        async with async_session_factory() as db:
            await db.execute(
                update(MediaSubscription)
                .where(MediaSubscription.id == subscription_id)
                .values(last_check_time=_now(), last_error=None, updated_time=_now())
            )
            await db.commit()

        logger.info(
            "media sync #%s (%s): scanned=%d new=%d queued=%d",
            subscription_id, sub.provider, len(items), len(new_items), len(queued),
        )
        return {
            "status": "ok",
            "subscription_id": subscription_id,
            "scanned": len(items),
            "new": len(new_items),
            "queued": len(queued),
        }

    # ── helpers ───────────────────────────────────────────────────────
    async def _known_ids(self, provider: str) -> set[str]:
        async with async_session_factory() as db:
            rows = await db.execute(
                select(MediaDownloadHistory.media_id).where(
                    MediaDownloadHistory.provider == provider,
                    MediaDownloadHistory.status == "completed",
                )
            )
            done = {r[0] for r in rows.all() if r[0]}
            # avoid re-queuing items already pending/downloading
            rows = await db.execute(
                select(MediaDownloadTask.media_id).where(
                    MediaDownloadTask.provider == provider,
                    MediaDownloadTask.status.in_(
                        ("pending", "downloading", "paused", "processing")
                    ),
                )
            )
            inflight = {r[0] for r in rows.all() if r[0]}
        return done | inflight

    async def _create_task(self, sub, item: MediaItem) -> int | None:
        async with async_session_factory() as db:
            row = MediaDownloadTask(
                subscription_id=sub.id,
                subscription_name=sub.name,
                provider=sub.provider,
                media_id=item.media_id,
                title=item.title,
                source_url=item.url,
                channel_name=item.channel,
                playlist_name=item.playlist,
                item_index=item.index,
                duration=item.duration,
                status="pending",
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row.id

    async def _record_error(self, subscription_id: int, message: str) -> None:
        try:
            async with async_session_factory() as db:
                await db.execute(
                    update(MediaSubscription)
                    .where(MediaSubscription.id == subscription_id)
                    .values(last_error=message[:2000], last_check_time=_now(), updated_time=_now())
                )
                await db.commit()
        except Exception:
            logger.debug("failed to record media sync error", exc_info=True)

    # ── manual re-download ────────────────────────────────────────────
    async def redownload(self, history_id: int) -> dict:
        async with async_session_factory() as db:
            hist = await db.get(MediaDownloadHistory, history_id)
            if hist is None:
                return {"status": "not_found"}
            sub = await db.get(MediaSubscription, hist.subscription_id) if hist.subscription_id else None
            if sub is None:
                return {"status": "no_subscription", "message": "原订阅已删除，无法重新下载"}
            db.expunge(sub)

            task = MediaDownloadTask(
                subscription_id=sub.id,
                subscription_name=sub.name,
                provider=sub.provider,
                media_id=hist.media_id,
                title=hist.title,
                source_url=hist.source_url or sub.url,
                channel_name=hist.channel_name,
                playlist_name=hist.playlist_name,
                duration=hist.duration,
                status="pending",
            )
            db.add(task)
            await db.commit()
            await db.refresh(task)
            task_id = task.id

        manager.submit(task_id)
        return {"status": "queued", "task_id": task_id}


# ══════════════════════════════════════════════════════════════════════
#  Scheduler entry points
# ══════════════════════════════════════════════════════════════════════


async def run_subscription(subscription_id: int) -> dict:
    """APScheduler callback for a single subscription."""
    return await MediaSyncJob().sync_subscription(subscription_id)


async def run_all() -> dict:
    """Sweep every enabled subscription (manual / global fallback)."""
    async with async_session_factory() as db:
        rows = await db.execute(
            select(MediaSubscription.id).where(MediaSubscription.enabled.is_(True))
        )
        ids = [r[0] for r in rows.all()]

    job = MediaSyncJob()
    summary = {"total": len(ids), "queued": 0, "errors": 0}
    for sub_id in ids:
        outcome = await job.sync_subscription(sub_id)
        if outcome.get("status") == "ok":
            summary["queued"] += int(outcome.get("queued", 0))
        elif outcome.get("status") not in ("disabled", "already_running"):
            summary["errors"] += 1

    try:
        await cleanup_finished_tasks(int(settings.MEDIA_RETENTION_DAYS or 30))
    except Exception:
        logger.debug("media task cleanup failed", exc_info=True)
    return summary


async def cleanup_finished_tasks(days: int = 30) -> int:
    if days <= 0:
        return 0
    cutoff = _now() - timedelta(days=days)
    async with async_session_factory() as db:
        result = await db.execute(
            delete(MediaDownloadTask).where(
                MediaDownloadTask.status.in_(("completed", "cancelled")),
                MediaDownloadTask.updated_time < cutoff,
            )
        )
        await db.commit()
        return int(result.rowcount or 0)


__all__ = [
    "MediaSyncJob",
    "cleanup_finished_tasks",
    "run_all",
    "run_subscription",
]

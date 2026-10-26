"""Download queue with pause / resume / cancel / retry / concurrency control.

Model
-----
Each row in ``media_download_tasks`` is the durable state; this manager keeps
the *runtime* side (asyncio task, control flags, semaphore).

* **pause**  → abort the running yt-dlp process, keep the partial file,
  mark ``paused``. Resume re-runs yt-dlp with ``--continue`` (断点续传).
* **cancel** → abort and mark ``cancelled`` (no history row).
* **retry**  → reset to ``pending`` and re-queue.
* **concurrency** → ``MEDIA_MAX_CONCURRENT`` worker slots.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.core.config import settings
from app.core.database import async_session_factory
from app.models import MediaDownloadHistory, MediaDownloadTask, MediaSubscription
from app.services.media_center.base import (
    DownloadOptions,
    MediaItem,
    build_provider,
    resolve_options,
)

logger = logging.getLogger("naspilot.media.queue")


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class _Control:
    """Runtime control flags for one task."""

    abort: bool = False
    reason: str = ""  # paused | cancelled


class MediaQueueManager:
    """Singleton-ish manager (one instance per process)."""

    def __init__(self) -> None:
        self._sem: asyncio.Semaphore | None = None
        self._tasks: dict[int, asyncio.Task] = {}
        self._controls: dict[int, _Control] = {}

    # ── helpers ───────────────────────────────────────────────────────
    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            limit = max(1, int(settings.MEDIA_MAX_CONCURRENT or 2))
            self._sem = asyncio.Semaphore(limit)
        return self._sem

    def active_count(self) -> int:
        return len([t for t in self._tasks.values() if not t.done()])

    def control(self, task_id: int) -> _Control:
        return self._controls.setdefault(task_id, _Control())

    # ── lifecycle ─────────────────────────────────────────────────────
    def submit(self, task_id: int) -> bool:
        """Schedule a pending task for execution."""
        existing = self._tasks.get(task_id)
        if existing and not existing.done():
            return False
        self._controls[task_id] = _Control()
        task = asyncio.create_task(self._run(task_id))
        self._tasks[task_id] = task
        task.add_done_callback(lambda t, tid=task_id: self._tasks.pop(tid, None))
        return True

    def is_active(self, task_id: int) -> bool:
        task = self._tasks.get(task_id)
        return bool(task and not task.done())

    def pause(self, task_id: int) -> bool:
        if not self.is_active(task_id):
            return False
        ctl = self.control(task_id)
        if ctl.abort:
            return False
        ctl.abort = True
        ctl.reason = "paused"
        return True

    def cancel(self, task_id: int) -> bool:
        if not self.is_active(task_id):
            return False
        ctl = self.control(task_id)
        ctl.abort = True
        ctl.reason = "cancelled"
        # the line hook terminates the child process gracefully
        return True

    def resume(self, task_id: int) -> bool:
        self._controls[task_id] = _Control()
        return self.submit(task_id)

    def retry(self, task_id: int) -> bool:
        self._controls[task_id] = _Control()
        return self.submit(task_id)

    # ── execution ─────────────────────────────────────────────────────
    async def _run(self, task_id: int) -> None:
        async with self._semaphore():
            ctl = self.control(task_id)
            row, sub = await self._load(task_id)
            if row is None or sub is None:
                return

            options = resolve_options(sub)
            provider = build_provider(_SubSnapshot(sub, task_id))
            item = MediaItem(
                media_id=row.media_id,
                title=row.title or row.media_id,
                url=row.source_url or sub.url,
                channel=row.channel_name or sub.name,
                playlist=row.playlist_name,
                index=row.item_index,
                duration=row.duration,
            )

            await self._set(
                task_id,
                status="downloading",
                started_at=_now(),
                attempts=(row.attempts or 0) + 1,
                error_message=None,
            )

            last_emit = 0.0

            async def on_progress(percent, speed, downloaded, total, eta, status):
                nonlocal last_emit
                now = _now().timestamp()
                if status == "downloading" and now - last_emit < 0.5 and percent < 100:
                    return
                last_emit = now
                await self._set(
                    task_id,
                    progress=round(float(percent), 1),
                    speed=speed,
                    downloaded_bytes=int(downloaded or 0),
                    total_bytes=int(total or 0),
                    eta=eta,
                    status="processing" if status == "processing" else "downloading",
                )

            async def on_line(_line: str) -> bool | None:
                if ctl.abort:
                    return False
                return None

            try:
                result = await provider.download(
                    item, options, on_progress=on_progress, on_line=on_line
                )
            except asyncio.CancelledError:
                await self._finish_cancelled(task_id)
                raise
            except NotImplementedError as exc:
                await self._set(task_id, status="failed", error_message=str(exc)[:800], finished_at=_now())
                return
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("download crashed for task %s", task_id)
                await self._set(
                    task_id, status="failed", error_message=f"{type(exc).__name__}: {exc}"[:800],
                    finished_at=_now(),
                )
                return

            if ctl.abort:
                if ctl.reason == "cancelled":
                    await self._finish_cancelled(task_id)
                else:
                    await self._set(task_id, status="paused")
                return

            if result.status == "cancelled":
                await self._finish_cancelled(task_id)
                return

            if result.status != "completed":
                await self._set(
                    task_id,
                    status="failed",
                    progress=0.0,
                    error_message=(result.error or "下载失败")[:800],
                    finished_at=_now(),
                )
                return

            await self._archive(sub, task_id, item, result)
            await self._set(
                task_id,
                status="completed",
                progress=100.0,
                speed=None,
                eta=None,
                finished_at=_now(),
                error_message=None,
            )

    async def _finish_cancelled(self, task_id: int) -> None:
        await self._set(task_id, status="cancelled", speed=None, eta=None, finished_at=_now())

    # ── persistence ───────────────────────────────────────────────────
    async def _load(self, task_id: int):
        async with async_session_factory() as db:
            row = await db.get(MediaDownloadTask, task_id)
            if row is None:
                return None, None
            sub = await db.get(MediaSubscription, row.subscription_id) if row.subscription_id else None
            if sub is None:
                return None, None
            db.expunge(row)
            db.expunge(sub)
        return row, sub

    async def _set(self, task_id: int, **fields) -> None:
        try:
            async with async_session_factory() as db:
                await db.execute(
                    update(MediaDownloadTask)
                    .where(MediaDownloadTask.id == task_id)
                    .values(updated_time=_now(), **fields)
                )
                await db.commit()
        except Exception:
            logger.debug("failed to update task %s", task_id, exc_info=True)

    async def _archive(self, sub: MediaSubscription, task_id: int, item: MediaItem, result) -> None:
        """Upsert the archive row (de-dup by provider + media_id)."""
        async with async_session_factory() as db:
            existing = (
                await db.execute(
                    select(MediaDownloadHistory).where(
                        MediaDownloadHistory.provider == sub.provider,
                        MediaDownloadHistory.media_id == item.media_id,
                    )
                )
            ).scalar_one_or_none()

            provider = build_provider(_SubSnapshot(sub, task_id))
            payload = {
                "title": result.title or item.title,
                "channel_name": provider.channel_name(item),
                "playlist_name": provider.playlist_name(item),
                "publish_date": item.publish_date,
                "download_time": _now(),
                "file_path": result.file_path,
                "file_size": result.file_size,
                "status": "completed",
                "media_type": result.media_type,
                "duration": result.duration or item.duration,
                "source_url": item.url,
                "subscription_id": sub.id,
            }
            if existing is None:
                db.add(
                    MediaDownloadHistory(
                        provider=sub.provider, media_id=item.media_id, **payload
                    )
                )
            else:
                for key, value in payload.items():
                    setattr(existing, key, value)
            await db.commit()


class _SubSnapshot:
    """Adapt a detached ORM row to the ``SubscriptionLike`` protocol."""

    def __init__(self, row: MediaSubscription, task_id: int) -> None:
        self.id = row.id
        self.name = row.name
        self.provider = row.provider
        self.url = row.url
        self.type = row.type
        self.enabled = row.enabled
        self.media_mode = row.media_mode
        self.audio_only = row.audio_only
        self.video_height = row.video_height
        self.audio_format = row.audio_format
        self.download_path = row.download_path
        self.number_prefix = row.number_prefix
        self.rate_limit = row.rate_limit
        self.max_items_per_sync = row.max_items_per_sync
        self.network_mode = row.network_mode
        self.proxy_url = row.proxy_url
        self.proxy_username = row.proxy_username
        self.proxy_password = row.proxy_password
        self.vpn_container = row.vpn_container
        self.task_id = task_id


#: process-wide manager
manager = MediaQueueManager()


async def enqueue_task(task_id: int) -> bool:
    """Submit a task (call after the row is committed)."""
    return manager.submit(task_id)


async def pause_task(task_id: int) -> dict:
    if not manager.pause(task_id):
        return {"status": "not_running", "message": "任务未在运行（可能已完成或在排队）"}
    await manager._set(task_id, status="pausing")
    return {"status": "pausing"}


async def cancel_task(task_id: int) -> dict:
    ok = manager.cancel(task_id)
    if not ok:
        await manager._set(task_id, status="cancelled", finished_at=_now())
        return {"status": "cancelled"}
    return {"status": "cancelling"}


async def resume_task(task_id: int) -> dict:
    async with async_session_factory() as db:
        row = await db.get(MediaDownloadTask, task_id)
        if row is None:
            return {"status": "not_found"}
        if row.status not in ("paused", "failed", "cancelled"):
            return {"status": "not_resumable", "current": row.status}
        await db.execute(
            update(MediaDownloadTask)
            .where(MediaDownloadTask.id == task_id)
            .values(status="pending", error_message=None, updated_time=_now())
        )
        await db.commit()
    manager.resume(task_id)
    return {"status": "resumed"}


async def retry_task(task_id: int) -> dict:
    async with async_session_factory() as db:
        row = await db.get(MediaDownloadTask, task_id)
        if row is None:
            return {"status": "not_found"}
        await db.execute(
            update(MediaDownloadTask)
            .where(MediaDownloadTask.id == task_id)
            .values(
                status="pending",
                progress=0,
                speed=None,
                eta=None,
                error_message=None,
                finished_at=None,
                updated_time=_now(),
            )
        )
        await db.commit()
    manager.retry(task_id)
    return {"status": "retrying"}


async def stats() -> dict:
    from sqlalchemy import func

    async with async_session_factory() as db:
        rows = await db.execute(
            select(MediaDownloadTask.status, func.count(MediaDownloadTask.id)).group_by(
                MediaDownloadTask.status
            )
        )
        counts = {row[0]: int(row[1]) for row in rows.all()}
    return {
        "active": manager.active_count(),
        "counts": counts,
        "concurrency": max(1, int(settings.MEDIA_MAX_CONCURRENT or 2)),
    }


__all__ = [
    "MediaQueueManager",
    "cancel_task",
    "enqueue_task",
    "manager",
    "pause_task",
    "resume_task",
    "retry_task",
    "stats",
]

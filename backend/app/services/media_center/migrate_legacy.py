"""One-time migration: legacy YouTube module → unified Media Download Center.

Background
----------
The first release shipped a YouTube-only module (``youtube_subscriptions`` /
``youtube_download_history``). It accidentally exposed non-YouTube types
(``bilibili``, ``podcast``, …) which could never work, and its scheduler kept
logging "尚未实现" for those rows — exactly the confusing behaviour users hit.

This migration makes the unified center the single owner of every source:

1. copy each legacy subscription into ``media_subscriptions``
   (mapping the legacy ``type`` onto a real ``provider``)
2. copy the download archive into ``media_download_history`` so already
   downloaded videos are never fetched twice
3. disable the legacy row (its scheduler no longer runs)

The function is idempotent — it can run on every startup.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.database import async_session_factory
from app.models import (
    MediaDownloadHistory,
    MediaSubscription,
    YoutubeDownloadHistory,
    YoutubeSubscription,
)

logger = logging.getLogger("naspilot.media.migrate")

#: legacy ``type`` → unified ``provider``
_TYPE_TO_PROVIDER = {
    "channel": "youtube",
    "playlist": "youtube",
    "youtube": "youtube",
    "bilibili": "bilibili",
    "podcast": "podcast",
    "ximalaya": "ximalaya",
    "xiaoyuzhou": "xiaoyuzhou",
    "rss": "custom",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def migrate_legacy_youtube() -> dict:
    """Copy legacy rows into the unified center (safe to call repeatedly)."""
    summary = {
        "subscriptions_migrated": 0,
        "subscriptions_skipped": 0,
        "history_migrated": 0,
        "legacy_disabled": 0,
    }

    async with async_session_factory() as db:
        legacy = (await db.execute(select(YoutubeSubscription))).scalars().all()

        for row in legacy:
            provider = _TYPE_TO_PROVIDER.get((row.type or "channel").lower(), "custom")

            # 1) subscription — de-duplicate on (provider, url)
            existing = (
                await db.execute(
                    select(MediaSubscription).where(
                        MediaSubscription.provider == provider,
                        MediaSubscription.url == row.url,
                    )
                )
            ).scalar_one_or_none()

            if existing is None:
                db.add(
                    MediaSubscription(
                        name=row.name,
                        provider=provider,
                        url=row.url,
                        type=row.type if row.type in ("video", "playlist", "channel") else "channel",
                        enabled=bool(row.enabled),
                        media_mode=row.media_mode or ("audio" if row.audio_only else "video"),
                        audio_only=bool(row.audio_only),
                        video_height=row.video_height or 0,
                        audio_format=row.audio_format or "mp3",
                        download_path=row.download_path,
                        schedule_type="every_6h",
                        last_check_time=row.last_check_time,
                        created_time=row.created_time or _now(),
                        updated_time=_now(),
                    )
                )
                summary["subscriptions_migrated"] += 1
                logger.info(
                    "migrated legacy subscription #%s (%s) → media center as %s",
                    row.id, row.name, provider,
                )
            else:
                summary["subscriptions_skipped"] += 1
                logger.debug(
                    "legacy subscription #%s already present in media center (%s)",
                    row.id, provider,
                )

            # 3) stop the legacy row so two schedulers never download the same item
            if row.enabled:
                row.enabled = False
                summary["legacy_disabled"] += 1

        # 2) archive — de-duplicate on (provider, media_id)
        known = {
            (r[0], r[1])
            for r in (
                await db.execute(
                    select(MediaDownloadHistory.provider, MediaDownloadHistory.media_id)
                )
            ).all()
        }
        legacy_history = (await db.execute(select(YoutubeDownloadHistory))).scalars().all()
        for hist in legacy_history:
            key = ("youtube", hist.video_id)
            if not hist.video_id or key in known:
                continue
            db.add(
                MediaDownloadHistory(
                    provider="youtube",
                    media_id=hist.video_id,
                    title=hist.title,
                    channel_name=hist.channel,
                    playlist_name=hist.playlist,
                    publish_date=hist.publish_date,
                    file_path=hist.file_path,
                    file_size=hist.file_size,
                    download_time=hist.download_time,
                    status=hist.status or "completed",
                    media_type=hist.media_type,
                    created_time=hist.download_time or hist.created_time or _now(),
                )
            )
            known.add(key)
            summary["history_migrated"] += 1

        await db.commit()

    if any(summary.values()):
        logger.info("legacy YouTube migration: %s", summary)
    return summary


__all__ = ["migrate_legacy_youtube"]

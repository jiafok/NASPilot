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

The legacy module has since been deleted, so the old tables are read with raw
SQL and only when they still exist. The function is idempotent — it can run on
every startup, and is a no-op for fresh installs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import sqlalchemy as sa

from app.core.database import async_session_factory
from app.core.config import settings

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


def _ts(value) -> datetime | None:
    """Normalise a stored timestamp (ISO string or datetime) for insertion."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


async def _table_exists(db, name: str) -> bool:
    if "sqlite" not in settings.DATABASE_URL:
        # Non-SQLite deployments never had the legacy module.
        return False
    row = await db.execute(
        sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": name},
    )
    return row.fetchone() is not None


async def migrate_legacy_youtube() -> dict:
    """Copy legacy rows into the unified center (safe to call repeatedly)."""
    summary = {
        "subscriptions_migrated": 0,
        "subscriptions_skipped": 0,
        "history_migrated": 0,
        "legacy_disabled": 0,
    }

    async with async_session_factory() as db:
        if not await _table_exists(db, "youtube_subscriptions"):
            return summary  # fresh install, nothing to move

        rows = (
            await db.execute(sa.text("SELECT * FROM youtube_subscriptions"))
        ).mappings().all()

        for row in rows:
            legacy_type = (row.get("type") or "channel").lower()
            provider = _TYPE_TO_PROVIDER.get(legacy_type, "custom")

            # 1) subscription — de-duplicate on (provider, url)
            existing = (
                await db.execute(
                    sa.text(
                        "SELECT id FROM media_subscriptions "
                        "WHERE provider = :p AND url = :u LIMIT 1"
                    ),
                    {"p": provider, "u": row.get("url")},
                )
            ).fetchone()

            if existing is None:
                mode = row.get("media_mode") or (
                    "audio" if row.get("audio_only") else "video"
                )
                await db.execute(
                    sa.text(
                        "INSERT INTO media_subscriptions ("
                        " name, provider, url, type, enabled, media_mode, audio_only,"
                        " video_height, audio_format, download_path, schedule_type,"
                        " last_check_time, created_time, updated_time"
                        ") VALUES ("
                        " :name, :provider, :url, :type, :enabled, :mode, :audio_only,"
                        " :height, :afmt, :path, 'every_6h', :last_check, :created, :updated"
                        ")"
                    ),
                    {
                        "name": row.get("name"),
                        "provider": provider,
                        "url": row.get("url"),
                        "type": row.get("type")
                        if row.get("type") in ("video", "playlist", "channel")
                        else "channel",
                        "enabled": 1 if row.get("enabled") else 0,
                        "mode": mode,
                        "audio_only": 1 if row.get("audio_only") else 0,
                        "height": row.get("video_height") or 0,
                        "afmt": row.get("audio_format") or "mp3",
                        "path": row.get("download_path"),
                        "last_check": _ts(row.get("last_check_time")),
                        "created": _ts(row.get("created_time")) or _now(),
                        "updated": _now(),
                    },
                )
                summary["subscriptions_migrated"] += 1
                logger.info(
                    "migrated legacy subscription #%s (%s) → media center as %s",
                    row.get("id"), row.get("name"), provider,
                )
            else:
                summary["subscriptions_skipped"] += 1

            # 3) stop the legacy row so two schedulers never download the same item
            if row.get("enabled"):
                await db.execute(
                    sa.text(
                        "UPDATE youtube_subscriptions SET enabled = 0 WHERE id = :i"
                    ),
                    {"i": row.get("id")},
                )
                summary["legacy_disabled"] += 1

        # 2) archive — de-duplicate on (provider, media_id)
        if await _table_exists(db, "youtube_download_history"):
            known = {
                (r[0], r[1])
                for r in (
                    await db.execute(
                        sa.text("SELECT provider, media_id FROM media_download_history")
                    )
                ).all()
            }
            hist_rows = (
                await db.execute(sa.text("SELECT * FROM youtube_download_history"))
            ).mappings().all()
            for hist in hist_rows:
                video_id = hist.get("video_id")
                if not video_id or ("youtube", video_id) in known:
                    continue
                await db.execute(
                    sa.text(
                        "INSERT INTO media_download_history ("
                        " provider, media_id, title, channel_name, playlist_name,"
                        " publish_date, file_path, file_size, download_time, status,"
                        " media_type, created_time"
                        ") VALUES ("
                        " 'youtube', :mid, :title, :channel, :playlist,"
                        " :publish, :file_path, :size, :dtime, :status,"
                        " :mtype, :created"
                        ")"
                    ),
                    {
                        "mid": video_id,
                        "title": hist.get("title"),
                        "channel": hist.get("channel"),
                        "playlist": hist.get("playlist"),
                        "publish": hist.get("publish_date"),
                        "file_path": hist.get("file_path"),
                        "size": hist.get("file_size") or 0,
                        "dtime": _ts(hist.get("download_time")),
                        "status": hist.get("status") or "completed",
                        "mtype": hist.get("media_type") or "video",
                        "created": _ts(hist.get("download_time"))
                        or _ts(hist.get("created_time"))
                        or _now(),
                    },
                )
                known.add(("youtube", video_id))
                summary["history_migrated"] += 1

        await db.commit()

    if any(summary.values()):
        logger.info("legacy YouTube migration: %s", summary)
    return summary


__all__ = ["migrate_legacy_youtube"]

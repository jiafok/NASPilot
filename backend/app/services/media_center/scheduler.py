"""Per-subscription scheduling for the Media Download Center.

Schedules live in APScheduler (the same instance the rest of NASPilot uses) so
restarts simply re-register from the database.
"""

from __future__ import annotations

import logging

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.scheduler.scheduler_service import get_scheduler
from app.services.media_center.job import run_subscription

logger = logging.getLogger("naspilot.media.scheduler")

JOB_PREFIX = "media-sub-"

_INTERVALS = {
    "hourly": {"hours": 1},
    "every_6h": {"hours": 6},
    "every_12h": {"hours": 12},
    "daily": {"days": 1},
}


def job_id(subscription_id: int) -> str:
    return f"{JOB_PREFIX}{subscription_id}"


def build_trigger(subscription):
    """Translate ``schedule_type`` / ``cron_expr`` into an APScheduler trigger."""
    kind = (getattr(subscription, "schedule_type", None) or "manual").lower()
    if kind == "manual":
        return None
    if kind in _INTERVALS:
        return IntervalTrigger(**_INTERVALS[kind], timezone="Asia/Shanghai")
    if kind == "cron":
        expr = (getattr(subscription, "cron_expr", None) or "").strip()
        if not expr:
            return None
        try:
            return CronTrigger.from_crontab(expr, timezone="Asia/Shanghai")
        except Exception:
            logger.exception("invalid cron for media subscription: %s", expr)
            return None
    logger.warning("unknown schedule_type %r — treated as manual", kind)
    return None


def upsert_subscription_schedule(subscription) -> bool:
    """Add / replace / remove the job for one subscription."""
    sched = get_scheduler()
    jid = job_id(subscription.id)
    try:
        sched.remove_job(jid)
    except Exception:
        pass

    if not getattr(subscription, "enabled", True):
        return False

    trigger = build_trigger(subscription)
    if trigger is None:
        return False

    sched.add_job(
        run_subscription,
        trigger=trigger,
        args=[subscription.id],
        id=jid,
        name=f"media-sync-{subscription.name}",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=600,
    )
    logger.info(
        "media schedule registered: %s (%s)", jid, getattr(subscription, "schedule_type", "?")
    )
    return True


def remove_subscription_schedule(subscription_id: int) -> None:
    try:
        get_scheduler().remove_job(job_id(subscription_id))
    except Exception:
        pass


async def sync_all_schedules() -> int:
    """Register every enabled subscription at startup."""
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models import MediaSubscription

    count = 0
    async with async_session_factory() as db:
        rows = await db.execute(select(MediaSubscription).where(MediaSubscription.enabled.is_(True)))
        for sub in rows.scalars().all():
            db.expunge(sub)
            if upsert_subscription_schedule(sub):
                count += 1
    logger.info("media schedules registered: %d", count)
    return count


__all__ = [
    "build_trigger",
    "job_id",
    "remove_subscription_schedule",
    "sync_all_schedules",
    "upsert_subscription_schedule",
]

"""APScheduler-based task scheduler — replaces system cron.

Tasks managed in DB are synced to APScheduler on startup and when tasks are
created/edited/deleted via the API.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.executors.asyncio import AsyncIOExecutor

from app.core.config import settings
from app.core.database import async_session_factory

if TYPE_CHECKING:
    from app.models import Task

logger = logging.getLogger("naspilot.scheduler")

scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    """Get (or lazily create) the global scheduler instance."""
    global scheduler
    if scheduler is None:
        scheduler = AsyncIOScheduler(
            executors={"default": AsyncIOExecutor()},
            timezone="Asia/Shanghai",
        )
    return scheduler


async def _execute_task(task_id: int) -> None:
    """Internal callback — loads a Task from DB and runs it."""
    from app.models import Task
    from app.services.task_service import run_task
    from sqlalchemy import select

    async with async_session_factory() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task_obj = result.scalar_one_or_none()
        if not task_obj or not task_obj.enabled:
            return
        logger.info(f"Executing scheduled task: {task_obj.name}")
        await run_task(db, task_obj, triggered_by="scheduler")


def _job_id(task_id: int) -> str:
    return f"task-{task_id}"


def add_task_to_scheduler(sched: AsyncIOScheduler, task: "Task") -> bool:
    """Add or replace a task's job in the scheduler."""
    if not task.cron_expr:
        return False
    job_id = _job_id(task.id)
    try:
        sched.remove_job(job_id)
    except Exception:
        pass
    trigger = CronTrigger.from_crontab(task.cron_expr, timezone=task.timezone or "Asia/Shanghai")
    sched.add_job(
        _execute_task,
        trigger=trigger,
        args=[task.id],
        id=job_id,
        name=task.name,
        replace_existing=True,
        misfire_grace_time=60,
        coalesce=True,       # skip queued runs if previous still running
        max_instances=1,     # at most 1 concurrent run per job
    )
    logger.info(f"Registered task '{task.name}' (id={task.id}) with cron '{task.cron_expr}'")
    return True


def remove_task_from_scheduler(sched: AsyncIOScheduler, task_id: int) -> None:
    """Remove a task's job from the scheduler."""
    try:
        sched.remove_job(_job_id(task_id))
    except Exception:
        pass


async def sync_all_tasks() -> int:
    """Load all enabled tasks from DB and register them."""
    from app.models import Task
    from sqlalchemy import select

    sched = get_scheduler()
    count = 0
    async with async_session_factory() as db:
        result = await db.execute(select(Task).where(Task.enabled.is_(True)))
        tasks = result.scalars().all()
        for task in tasks:
            if add_task_to_scheduler(sched, task):
                count += 1
    logger.info(f"Synced {count} tasks to scheduler")
    return count


async def start_scheduler() -> None:
    """Initialize and start the scheduler + sync tasks + sync plugin schedules."""
    sched = get_scheduler()
    if not sched.running:
        sched.start()
    await sync_all_tasks()
    await sync_plugin_schedules()


async def shutdown_scheduler() -> None:
    """Gracefully shutdown the scheduler."""
    global scheduler
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        scheduler = None


# ── Plugin scheduled runs ─────────────────────────────────────────────

def _plugin_job_id(plugin_id: int, instance_id: int) -> str:
    return f"plugin-{plugin_id}-{instance_id}"


# Per-instance locks — prevents concurrent scheduled runs of the same plugin
_plugin_locks: dict[str, asyncio.Lock] = {}


async def _execute_plugin(plugin_id: int, instance_id: int) -> None:
    """Internal callback — loads a PluginInstance from DB and runs it.

    Uses a per-instance lock to prevent duplicate concurrent executions
    when multiple scheduler jobs or overlapping triggers fire at once.
    """
    job_id = _plugin_job_id(plugin_id, instance_id)
    lock = _plugin_locks.setdefault(job_id, asyncio.Lock())

    if lock.locked():
        logger.debug("Plugin %s already running — skipping duplicate trigger", job_id)
        return

    async with lock:
        from app.models import Plugin, PluginInstance
        from app.plugins.registry import registry
        from sqlalchemy import select

        async with async_session_factory() as db:
            # Load instance
            r = await db.execute(select(PluginInstance).where(PluginInstance.id == instance_id))
            inst = r.scalar_one_or_none()
            if not inst or not inst.enabled:
                return

            # Load plugin metadata
            r2 = await db.execute(select(Plugin).where(Plugin.id == plugin_id))
            p = r2.scalar_one_or_none()
            if not p:
                return

            # Find runtime class
            plugin_cls = None
            for slug, cls in registry.list_all():
                if slug == p.slug:
                    plugin_cls = cls
                    break
            if plugin_cls is None:
                return

            # Check schedule enabled in config
            cfg = inst.config or {}
            if not cfg.get("schedule_enabled"):
                return

            logger.info(f"Scheduled plugin run: {p.name} ({inst.name})")
            runtime = plugin_cls(cfg)
            result = await runtime.run()

            # Save run history (same as manual trigger)
            import json
            from datetime import datetime as _dt, timezone as _tz
            from sqlalchemy.orm.attributes import flag_modified as _flag_modified
            now_iso = _dt.now(_tz.utc).isoformat()
            state = runtime.config.setdefault("state", {})
            history: list = state.setdefault("run_history", [])
            history.insert(0, {
                "time": now_iso,
                "trigger": "scheduled",
                "status": result.get("status", "ok"),
                "added": result.get("added", 0),
                "error": result.get("error", ""),
                "summary": json.dumps({k: v for k, v in result.items()
                    if k not in ("added_messages", "failed_messages", "deleted_messages", "skipped_messages")},
                    ensure_ascii=False, default=str),
            })
            state["run_history"] = history[:50]
            inst.config = runtime.config
            _flag_modified(inst, "config")  # SQLAlchemy JSON column needs explicit dirty flag
            await db.commit()
            logger.info(f"Scheduled plugin run complete: {p.name} status={result.get('status')}")


def upsert_plugin_schedule(sched: AsyncIOScheduler, plugin_id: int, instance_id: int, config: dict) -> bool:
    """Add, update, or remove a plugin schedule based on config."""
    job_id = _plugin_job_id(plugin_id, instance_id)
    cron_expr = config.get("schedule_cron", "")
    enabled = config.get("schedule_enabled", False)

    # Remove existing job
    try:
        sched.remove_job(job_id)
    except Exception:
        pass

    if not enabled or not cron_expr:
        return False

    try:
        trigger = CronTrigger.from_crontab(cron_expr, timezone="Asia/Shanghai")
        sched.add_job(
            _execute_plugin,
            trigger=trigger,
            args=[plugin_id, instance_id],
            id=job_id,
            name=f"plugin-{plugin_id}-{instance_id}",
            replace_existing=True,
            misfire_grace_time=60,
            coalesce=True,       # skip queued runs if previous still running
            max_instances=1,     # at most 1 concurrent run per job
        )
        logger.info(f"Registered plugin schedule: id={job_id} cron={cron_expr}")
        return True
    except Exception:
        logger.exception(f"Invalid cron for plugin schedule: {cron_expr}")
        return False


async def sync_plugin_schedules() -> int:
    """Load all plugin instances with schedule_cron and register them."""
    from app.models import PluginInstance
    from sqlalchemy import select

    sched = get_scheduler()
    count = 0
    async with async_session_factory() as db:
        result = await db.execute(select(PluginInstance).where(PluginInstance.enabled.is_(True)))
        instances = result.scalars().all()
        for inst in instances:
            cfg = inst.config or {}
            if upsert_plugin_schedule(sched, inst.plugin_id, inst.id, cfg):
                count += 1
    logger.info(f"Synced {count} plugin schedules")
    return count

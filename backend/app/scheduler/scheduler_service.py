"""APScheduler-based task scheduler — replaces system cron.

Tasks managed in DB are synced to APScheduler on startup and when tasks are
created/edited/deleted via the API.
"""

import logging
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

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
            executors={"default": {"type": "threadpool", "max_workers": settings.SCHEDULER_THREADPOOL}},
            timezone=settings.APP_NAME,  # placeholder, replaced below
        )
        scheduler.timezone = "Asia/Shanghai"
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
    """Initialize and start the scheduler + sync tasks."""
    sched = get_scheduler()
    if not sched.running:
        sched.start()
    await sync_all_tasks()


async def shutdown_scheduler() -> None:
    """Gracefully shutdown the scheduler."""
    global scheduler
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        scheduler = None

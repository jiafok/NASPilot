"""Task execution service — runs shell/python/docker commands."""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, TaskExecution

logger = logging.getLogger("naspilot.task")


async def run_task(db: AsyncSession, task: Task, triggered_by: str = "manual") -> TaskExecution:
    """Execute a task with timeout and capture stdout/stderr."""
    execution = TaskExecution(task_id=task.id, status="running", triggered_by=triggered_by)
    db.add(execution)
    await db.flush()
    start = datetime.now(timezone.utc)

    try:
        args = []
        if task.args:
            import json

            args = json.loads(task.args)

        proc = await asyncio.create_subprocess_exec(
            task.command,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=task.working_dir or None,
            env=task.env_vars or None,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=task.timeout or None)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            execution.status = "timeout"
            execution.exit_code = -1
            execution.error_message = f"Timeout after {task.timeout}s"
        else:
            execution.exit_code = proc.returncode
            execution.stdout = stdout_b.decode("utf-8", errors="replace")[:65536]
            execution.stderr = stderr_b.decode("utf-8", errors="replace")[:65536]
            execution.status = "success" if proc.returncode == 0 else "failed"
    except Exception as e:
        execution.status = "failed"
        execution.exit_code = -1
        execution.error_message = str(e)
        logger.exception(f"Task {task.name} execution error")
    finally:
        end = datetime.now(timezone.utc)
        execution.end_time = end
        execution.duration_ms = int((end - start).total_seconds() * 1000)
        task.last_run_at = end
        await db.commit()
        return execution

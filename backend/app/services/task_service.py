"""Task execution service — unified script runner with structured logging.

Supports three execution modes:
  shell  → /bin/sh -c <command>
  python → python3 <command>
  docker → docker exec <container> <command>  or  docker run ... <command>
"""

import asyncio
import logging
import os
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, TaskExecution

logger = logging.getLogger("naspilot.task")

LOG_DIR = Path("/app/logs")
MAX_STDOUT = 65536
MAX_STDERR = 65536


def _build_cmd(task: Task) -> list[str]:
    """Build the actual shell command list based on task_type."""
    env_vars = task.env_vars or {}
    extra_args = json.loads(task.args) if task.args else []

    if task.task_type == "shell":
        # Simulate cron_run.sh pattern: /bin/sh -c "command with args"
        cmd_parts = [task.command, *extra_args]
        return ["/bin/sh", "-c", " ".join(cmd_parts)]

    elif task.task_type == "python":
        return ["python3", task.command, *extra_args]

    elif task.task_type == "docker":
        # docker exec <container> <cmd> or docker run ... <cmd>
        return ["docker", "exec", task.command, *extra_args]

    else:
        raise ValueError(f"Unknown task_type: {task.task_type}")


def _write_unified_log(task_name: str, status: str, stdout: str, stderr: str, exit_code: int, duration_ms: int):
    """Write a structured log entry like cron_run.sh produces."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"{task_name.replace(' ', '_')}.log"

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    marker = "[START]" if status in ("running",) else "[END]"

    lines = [
        f"{now} {marker} {task_name}",
        f"  status={status}  exit_code={exit_code}  duration_ms={duration_ms}",
    ]
    if stdout.strip():
        lines.append(f"  --- stdout ({len(stdout)} bytes) ---")
        for line in stdout.strip().split("\n")[-50:]:
            lines.append(f"  {line}")
    if stderr.strip():
        lines.append(f"  --- stderr ({len(stderr)} bytes) ---")
        for line in stderr.strip().split("\n")[-50:]:
            lines.append(f"  {line}")

    with open(log_file, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n\n")


async def run_task(db: AsyncSession, task: Task, triggered_by: str = "manual") -> TaskExecution:
    """Execute a task with timeout, capture output, and write unified log."""
    execution = TaskExecution(task_id=task.id, status="running", triggered_by=triggered_by)
    db.add(execution)
    await db.flush()
    start = datetime.now(timezone.utc)

    try:
        cmd = _build_cmd(task)
        cwd = task.working_dir or os.getenv("HOME", "/")
        merged_env = {**os.environ, **(task.env_vars or {})}

        logger.info(f"[{task.name}] Executing: {' '.join(cmd)}  cwd={cwd}")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=merged_env,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=task.timeout or None
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            execution.status = "timeout"
            execution.exit_code = -1
            execution.error_message = f"Timeout after {task.timeout}s"
            stdout_b, stderr_b = b"", f"Timeout after {task.timeout}s".encode()
        else:
            execution.exit_code = proc.returncode or 0
            execution.stdout = stdout_b.decode("utf-8", errors="replace")[:MAX_STDOUT]
            execution.stderr = stderr_b.decode("utf-8", errors="replace")[:MAX_STDERR]
            execution.status = "success" if execution.exit_code == 0 else "failed"
            if execution.exit_code != 0 and execution.stderr:
                execution.error_message = execution.stderr[:500]

        # Write unified log
        stdout_s = execution.stdout or ""
        stderr_s = execution.stderr or ""
        _write_unified_log(task.name, execution.status, stdout_s, stderr_s,
                           execution.exit_code or 0, 0)

    except Exception as e:
        execution.status = "failed"
        execution.exit_code = -1
        execution.error_message = str(e)
        logger.exception(f"Task [{task.name}] execution error")
        _write_unified_log(task.name, "failed", "", str(e), -1, 0)

    finally:
        end = datetime.now(timezone.utc)
        execution.end_time = end
        execution.duration_ms = int((end - start).total_seconds() * 1000)
        task.last_run_at = end
        await db.commit()

        # Notify on failure
        if execution.status in ("failed", "timeout"):
            try:
                await _notify_failure(task, execution)
            except Exception:
                logger.exception("Failed to send failure notification")

    return execution


async def _notify_failure(task: Task, execution: TaskExecution) -> None:
    """Send failure alert to default notification channels."""
    from app.models.notification import NotificationChannel
    from app.services.notification_service import send_notification

    async with async_session_factory() as db2:
        result = await db2.execute(
            select(NotificationChannel).where(
                NotificationChannel.enabled.is_(True),
                NotificationChannel.is_default.is_(True),
            )
        )
        channels = result.scalars().all()
        if not channels:
            return
        msg = (
            f"❌ Task Failed: {task.name}\n"
            f"Status: {execution.status}\n"
            f"Exit Code: {execution.exit_code}\n"
            f"Error: {execution.error_message or 'N/A'}\n"
            f"Time: {execution.end_time}"
        )
        for ch in channels:
            await send_notification(ch, msg)


# Import for _notify_failure
from app.core.database import async_session_factory

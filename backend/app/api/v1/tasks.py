"""Task endpoints — CRUD + manual trigger + execution history."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import CurrentUser
from app.models import Task, TaskExecution
from app.schemas.task import TaskCreate, TaskExecutionOut, TaskOut, TaskUpdate
from app.scheduler.scheduler_service import (
    add_task_to_scheduler,
    get_scheduler,
    remove_task_from_scheduler,
)
from app.services.notification_service import notify_default_channels
from app.services.task_service import run_task

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/executions", response_model=list[dict], summary="Recent executions across all tasks")
async def list_recent_executions(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(10, ge=1, le=100),
):
    """Return recent task executions for the Dashboard, including task_name."""
    from sqlalchemy.orm import joinedload

    result = await db.execute(
        select(TaskExecution)
        .options(joinedload(TaskExecution.task))
        .order_by(TaskExecution.id.desc())
        .limit(limit)
    )
    executions = result.scalars().all()
    return [
        {
            "id": e.id,
            "task_id": e.task_id,
            "task_name": e.task.name if e.task else "Unknown",
            "status": e.status,
            "start_time": e.start_time.isoformat() if e.start_time else None,
            "duration_ms": e.duration_ms,
            "exit_code": e.exit_code,
            "triggered_by": e.triggered_by,
            "error_message": e.error_message,
        }
        for e in executions
    ]


@router.get("", response_model=list[TaskOut], summary="List all tasks")
async def list_tasks(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    enabled: bool | None = None,
    task_type: str | None = None,
):
    """List tasks, optionally filtered by enabled / type."""
    q = select(Task)
    if enabled is not None:
        q = q.where(Task.enabled.is_(enabled))
    if task_type:
        q = q.where(Task.task_type == task_type)
    result = await db.execute(q.order_by(Task.id))
    return result.scalars().all()


@router.post("", response_model=TaskOut, status_code=201, summary="Create task")
async def create_task(
    body: TaskCreate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    task = Task(**body.model_dump())
    db.add(task)
    await db.commit()
    await db.refresh(task)
    if task.enabled and task.cron_expr:
        add_task_to_scheduler(get_scheduler(), task)
    return task


@router.get("/{task_id}", response_model=TaskOut, summary="Get task")
async def get_task(task_id: int, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.put("/{task_id}", response_model=TaskOut, summary="Update task")
async def update_task(
    task_id: int,
    body: TaskUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(task, k, v)
    await db.commit()
    await db.refresh(task)
    remove_task_from_scheduler(get_scheduler(), task.id)
    if task.enabled and task.cron_expr:
        add_task_to_scheduler(get_scheduler(), task)
    return task


@router.delete("/{task_id}", summary="Delete task")
async def delete_task(task_id: int, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task_name = task.name
    remove_task_from_scheduler(get_scheduler(), task.id)
    await db.delete(task)
    await db.commit()
    # ── Notify ──
    await notify_default_channels(
        db,
        title="🗑️ 任务已删除",
        message=f"任务「{task_name}」(ID:{task_id}) 已被删除",
        level="warn",
        event_type="task_deleted",
    )
    return {"message": "deleted"}


@router.post("/{task_id}/run", response_model=TaskExecutionOut, summary="Run task now")
async def trigger_task(task_id: int, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return await run_task(db, task, triggered_by="manual")


@router.get("/{task_id}/executions", response_model=list[TaskExecutionOut], summary="Execution history")
async def list_executions(
    task_id: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    result = await db.execute(
        select(TaskExecution)
        .where(TaskExecution.task_id == task_id)
        .order_by(TaskExecution.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()

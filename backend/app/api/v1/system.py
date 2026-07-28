"""System endpoints — dashboard stats, logs, settings."""

import asyncio
import json
import os
import re
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import CurrentUser
from app.core.logging_config import LOG_FILE
from app.models import LogEntry, Setting
from app.schemas.system import (
    DockerActionRequest,
    DockerBulkActionRequest,
    DockerContainerOut,
    DockerExecRequest,
    DockerExecResult,
    DockerStatsOut,
    LogEntryOut,
    SettingBulkEntry,
    SettingOut,
    SettingUpdate,
    SystemStats,
)
from app.services.system_service import get_system_stats
from app.services.docker_service import (
    DockerException,
    NotFound,
    apply_container_action,
    bulk_container_action,
    exec_in_container,
    get_containers_stats,
    get_container_logs,
    list_containers,
)

router = APIRouter(prefix="/system", tags=["system"])

# Regex matching the formatted log line:
# "2026-07-23 16:30:09 [INFO    ] naspilot.plugin.pt_rss — message text"
LOG_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+"
    r"\[(\w+)\s*\]\s+"
    r"(\S+)\s+—\s+"
    r"(.*)$"
)


def _extract_source(logger_name: str) -> str:
    if "plugin" in logger_name:
        slug = logger_name.replace("naspilot.plugin.", "").replace("naspilot.plugins.", "")
        return slug  # e.g. "pt_rss", "alist_upload" — no "plugin:" prefix
    if "scheduler" in logger_name:
        return "scheduler"
    if "task" in logger_name:
        return "task"
    return "system"


def _parse_line(line: str) -> dict[str, Any] | None:
    m = LOG_RE.match(line.strip())
    if not m:
        return None
    return {
        "timestamp": m.group(1).strip(),
        "level": m.group(2).strip(),
        "logger": m.group(3).strip(),
        "source": _extract_source(m.group(3).strip()),
        "message": m.group(4).strip().replace("\r", ""),
    }


def _get_log_path() -> str:
    """Resolve the log file path, trying multiple locations."""
    if LOG_FILE and os.path.isfile(LOG_FILE):
        return LOG_FILE
    from app.core.config import settings
    import pathlib
    path = str(settings.LOG_DIR.resolve() / "naspilot.log")
    if os.path.isfile(path):
        return path
    app_dir = pathlib.Path(__file__).resolve().parent.parent.parent.parent
    path = str(app_dir / "data" / "logs" / "naspilot.log")
    return path


# ── Dashboard ───────────────────────────────────────────────────────────


@router.get("/stats", response_model=SystemStats, summary="System stats")
async def stats(user: CurrentUser):
    """Return real-time CPU/memory/disk/docker/qB stats for the dashboard."""
    return get_system_stats()


# ── Real-time metrics ────────────────────────────────────────────────────

from app.core.metrics import get_current, get_history


@router.get("/metrics/current", summary="Current metrics snapshot")
async def metrics_current(user: CurrentUser):
    """Latest metrics snapshot including disk partitions."""
    return get_current()


@router.get("/metrics/history", summary="Time-series metrics history")
async def metrics_history(
    user: CurrentUser,
    count: int = Query(300, ge=10, le=600, description="Number of samples (1 per second)"),
):
    """Return time-series data for real-time charts (CPU, memory, net, disk IO)."""
    return get_history(count)


# ── Logs (file-based, parsed into structured records) ────────────────────


@router.get("/logs", response_model=list[LogEntryOut], summary="Query logs")
async def list_logs(
    user: CurrentUser,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    level: str | None = None,
    source: str | None = None,
    search: str | None = None,
):
    """Query historical logs from the rotating log file.

    Filters are applied before pagination.  Returns parsed structured records.
    """
    log_path = _get_log_path()
    if not os.path.isfile(log_path):
        return []

    matched: list[dict[str, Any]] = []
    
    def _read_log_file() -> list[dict[str, Any]]:
        result = []
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                parsed = _parse_line(line)
                if parsed is None:
                    continue
                if level and parsed["level"].upper() != level.upper():
                    continue
                if source and parsed["source"] != source:
                    continue
                if search and search.lower() not in parsed["message"].lower():
                    continue
                result.append(parsed)
        return result
    
    matched = await asyncio.to_thread(_read_log_file)

    # Reverse: newest first (matching old DB ORDER BY id DESC)
    matched.reverse()
    page = matched[offset : offset + limit]

    results: list[LogEntryOut] = []
    for idx, entry in enumerate(page, start=offset + 1):
        try:
            ts = datetime.strptime(entry["timestamp"], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            ts = datetime.utcnow()
        results.append(LogEntryOut(
            id=idx,
            timestamp=ts,
            logger=entry["logger"],
            level=entry["level"],
            source=entry["source"],
            message=entry["message"],
        ))
    return results


# ── Raw log file (reads /app/data/logs/naspilot.log) ────────────────────


@router.get("/logs/raw", summary="Raw log file", response_class=PlainTextResponse)
async def raw_logs(
    source: str | None = None,
    level: str | None = None,
    limit: int = Query(10000, ge=100, le=100000),
):
    """Serve the raw log text file directly.

    Optional query params:
    - ``source`` : filter by source (e.g. ``plugin:pt_rss``)
    - ``level`` : filter by level (e.g. ``WARNING``)
    - ``limit`` : max lines (default 10000)
    """
    log_path = _get_log_path()
    if not os.path.isfile(log_path):
        return PlainTextResponse(f"Log file not found.\n", status_code=200)

    def _read_raw_log() -> str:
        lines: list[str] = []
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                if source and source not in stripped:
                    continue
                if level and f"[{level.upper()}" not in stripped:
                    continue
                lines.append(line)
                if len(lines) >= limit:
                    break
        return "".join(lines[-limit:])
    
    content = await asyncio.to_thread(_read_raw_log)
    return PlainTextResponse(content)


# ── Settings ────────────────────────────────────────────────────────────


@router.get("/settings", response_model=list[SettingOut], summary="List settings")
async def list_settings(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    category: str | None = None,
):
    q = select(Setting)
    if category:
        q = q.where(Setting.category == category)
    result = await db.execute(q.order_by(Setting.key))
    return result.scalars().all()


@router.get("/settings/public", response_model=list[SettingOut], summary="Public settings")
async def public_settings(db: Annotated[AsyncSession, Depends(get_db)]):
    """Settings visible without auth (e.g. app name, version)."""
    result = await db.execute(select(Setting).where(Setting.is_public.is_(True)))
    return result.scalars().all()


@router.put("/settings", summary="Batch update settings")
async def batch_update_settings(
    body: list[SettingBulkEntry],
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update multiple settings at once."""
    for entry in body:
        result = await db.execute(select(Setting).where(Setting.key == entry.key))
        setting = result.scalar_one_or_none()
        if setting:
            setting.value = entry.value
    await db.commit()
    return {"message": "saved", "count": len(body)}


@router.put("/settings/{key}", response_model=SettingOut, summary="Update setting")
async def update_setting(
    key: str,
    body: SettingUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    if not setting:
        raise HTTPException(status_code=404, detail="Setting not found")
    setting.value = body.value
    await db.commit()
    await db.refresh(setting)
    return setting


# ── Docker container management ──────────────────────────────────────────


@router.get("/docker/containers", response_model=list[DockerContainerOut], summary="List Docker containers")
async def docker_containers(user: CurrentUser, all: bool = Query(True, description="Include stopped containers")):
    try:
        # Wrap synchronous docker call in thread to avoid blocking event loop
        return await asyncio.to_thread(list_containers, include_all=all)
    except DockerException as exc:
        raise HTTPException(status_code=503, detail=f"Docker unavailable: {exc}")


@router.get("/docker/stats", response_model=list[DockerStatsOut], summary="Container resource stats")
async def docker_stats(user: CurrentUser, running_only: bool = Query(True, description="Only running containers")):
    try:
        # Wrap synchronous docker call in thread to avoid blocking event loop
        return await asyncio.to_thread(get_containers_stats, running_only=running_only)
    except DockerException as exc:
        raise HTTPException(status_code=503, detail=f"Docker unavailable: {exc}")


@router.get(
    "/docker/containers/{container_id}/logs",
    response_class=PlainTextResponse,
    summary="Read container logs",
)
async def docker_logs(
    container_id: str,
    user: CurrentUser,
    tail: int = Query(500, ge=10, le=5000),
    since: int | None = Query(None, ge=0, description="UNIX timestamp in seconds"),
):
    try:
        # Wrap synchronous docker call in thread to avoid blocking event loop
        text = await asyncio.to_thread(get_container_logs, container_id, tail, since)
        return PlainTextResponse(text)
    except NotFound:
        raise HTTPException(status_code=404, detail="Container not found")
    except DockerException as exc:
        raise HTTPException(status_code=503, detail=f"Docker unavailable: {exc}")


@router.post("/docker/containers/{container_id}/exec", response_model=DockerExecResult, summary="Execute command in container")
async def docker_exec(container_id: str, body: DockerExecRequest, user: CurrentUser):
    try:
        # Wrap synchronous docker call in thread to avoid blocking event loop
        return await asyncio.to_thread(exec_in_container, container_id, body.command, body.user, body.workdir)
    except NotFound:
        raise HTTPException(status_code=404, detail="Container not found")
    except DockerException as exc:
        raise HTTPException(status_code=503, detail=f"Docker unavailable: {exc}")


@router.post("/docker/containers/{container_id}/action", summary="Container lifecycle action")
async def docker_action(container_id: str, body: DockerActionRequest, user: CurrentUser):
    try:
        # Wrap synchronous docker call in thread to avoid blocking event loop
        return await asyncio.to_thread(apply_container_action, container_id, body.action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except NotFound:
        raise HTTPException(status_code=404, detail="Container not found")
    except DockerException as exc:
        raise HTTPException(status_code=503, detail=f"Docker unavailable: {exc}")


@router.post("/docker/containers/bulk-action", summary="Bulk container lifecycle action")
async def docker_bulk_action(body: DockerBulkActionRequest, user: CurrentUser):
    if not body.container_ids:
        raise HTTPException(status_code=400, detail="container_ids is required")
    try:
        # Wrap synchronous docker call in thread to avoid blocking event loop
        return await asyncio.to_thread(bulk_container_action, body.container_ids, body.action)
    except DockerException as exc:
        raise HTTPException(status_code=503, detail=f"Docker unavailable: {exc}")

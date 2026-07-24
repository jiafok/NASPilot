"""System endpoints — dashboard stats, logs, settings."""

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
from app.schemas.system import LogEntryOut, SettingOut, SettingUpdate, SystemStats
from app.services.system_service import get_system_stats

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
        return f"plugin:{slug}"
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
        "timestamp": m.group(1),
        "level": m.group(2),
        "logger": m.group(3),
        "source": _extract_source(m.group(3)),
        "message": m.group(4),
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
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            parsed = _parse_line(line)
            if parsed is None:
                # Unparsed line → include as raw SYSTEM message
                parsed = {
                    "timestamp": "unknown",
                    "level": "INFO",
                    "logger": "naspilot",
                    "source": "system",
                    "message": line.strip(),
                }
            if level and parsed["level"].upper() != level.upper():
                continue
            if source and parsed["source"] != source:
                continue
            if search and search.lower() not in parsed["message"].lower():
                continue
            matched.append(parsed)

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

    return PlainTextResponse("".join(lines[-limit:]))


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

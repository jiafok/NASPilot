"""WebSocket endpoints — real-time log streaming via file tailing."""

import asyncio
import json
import logging
import os
import re
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.database import async_session_factory
from app.core.deps import get_current_user_ws
from app.core.logging_config import LOG_FILE
from app.core.config import settings
import pathlib

router = APIRouter(tags=["websocket"])

# Regex to parse a formatted log line:
# "2026-07-23 16:30:09 [INFO    ] naspilot.plugin.pt_rss — message text"
LOG_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+"
    r"\[(\w+)\s*\]\s+"
    r"(\S+)\s+—\s+"
    r"(.*)$"
)


def _extract_source(logger_name: str) -> str:
    if logger_name.startswith("naspilot.plugin.") or logger_name.startswith("naspilot.plugins."):
        slug = logger_name.replace("naspilot.plugin.", "").replace("naspilot.plugins.", "")
        return f"plugin:{slug}"
    if "scheduler" in logger_name:
        return "scheduler"
    if "task" in logger_name:
        return "task"
    return "system"


def _parse_line(line: str) -> dict[str, Any] | None:
    """Parse a formatted log line into structured fields."""
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


class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, msg: dict[str, Any]) -> None:
        text = json.dumps(msg, default=str, ensure_ascii=False)
        stale = []
        for ws in self.active:
            try:
                await ws.send_text(text)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)


manager = ConnectionManager()


@router.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket):
    """Stream log entries in real-time by tailing the log file.

    Query params:
    - ``token`` : JWT auth
    - ``source`` : filter by source (e.g. ``plugin:pt_rss``)
    """
    async with async_session_factory() as db:
        user = await get_current_user_ws(websocket, db)
    if not user:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    source_filter = websocket.query_params.get("source")
    await manager.connect(websocket)

    app_dir = pathlib.Path(__file__).resolve().parent.parent
    log_path = str(app_dir / "data" / "logs" / "naspilot.log")
    if not os.path.isfile(log_path):
        log_path = str(settings.LOG_DIR.resolve() / "naspilot.log")
    if not os.path.isfile(log_path):
        tail_logger.warning("Log file not found: %s", log_path)
        try:
            while True:
                await asyncio.sleep(30)
                await websocket.send_text(json.dumps({"type": "ping"}))
        except WebSocketDisconnect:
            manager.disconnect(websocket)
        return

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(0, os.SEEK_END)  # start at end — live tail only
            while True:
                line = f.readline()
                if line:
                    entry = _parse_line(line)
                    if entry:
                        if source_filter and entry.get("source") != source_filter:
                            continue
                        await manager.broadcast({"type": "log", **entry})
                    continue
                # No new data — check for rotation (inode changed)
                try:
                    if os.stat(log_path).st_ino != os.fstat(f.fileno()).st_ino:
                        f.close()
                        f = open(log_path, "r", encoding="utf-8", errors="replace")
                        tail_logger.info("Log file rotated, re-opening")
                except Exception:
                    pass
                await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        tail_logger.exception("WS tailer error")
        manager.disconnect(websocket)

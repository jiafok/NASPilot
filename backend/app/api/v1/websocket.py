"""WebSocket endpoints — real-time log streaming via file tailing.

Architecture: a **single** global tailer reads the log file. Each WebSocket
connection registers a per-connection async queue. The tailer pushes parsed
entries to every queue; each connection's own send-loop drains its queue.
This avoids N tailers × N connections = N² duplicate broadcasts.
"""

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

logger = logging.getLogger("naspilot.websocket")

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


def _resolve_log_path() -> str:
    """Find the log file path — checks LOG_FILE global, settings.LOG_DIR, fallbacks."""
    if LOG_FILE and os.path.isfile(LOG_FILE):
        return LOG_FILE
    app_dir = pathlib.Path(__file__).resolve().parent.parent.parent.parent
    candidates = [
        str(settings.LOG_DIR.resolve() / "naspilot.log"),
        str(app_dir / "data" / "logs" / "naspilot.log"),
        str(app_dir / "logs" / "naspilot.log"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return candidates[0]  # return primary even if not yet created


class ConnectionManager:
    """Manages WebSocket connections and a single global log tailer.

    Each connected client gets a personal asyncio.Queue. The tailer pushes
    parsed log entries to all queues. Each connection's own send-loop drains
    its queue — no cross-talk, no N² duplication.
    """

    def __init__(self) -> None:
        self._subscribers: dict[WebSocket, asyncio.Queue] = {}
        self._tailer_task: asyncio.Task | None = None
        self._tailer_started = False

    async def connect(self, ws: WebSocket) -> asyncio.Queue:
        await ws.accept()
        q: asyncio.Queue = asyncio.Queue(maxsize=10000)
        self._subscribers[ws] = q
        # Start the single global tailer on first connection
        if not self._tailer_started:
            self._tailer_started = True
            self._tailer_task = asyncio.create_task(self._tail_log_file())
        return q

    def disconnect(self, ws: WebSocket) -> None:
        self._subscribers.pop(ws, None)

    async def _tail_log_file(self) -> None:
        """Single global file tailer — reads log file once, broadcasts to all."""
        log_path = _resolve_log_path()
        logger.info("WebSocket log tailer started: %s", log_path)

        while True:
            if not os.path.isfile(log_path):
                await asyncio.sleep(2)
                continue
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(0, os.SEEK_END)  # live tail only
                    while True:
                        line = f.readline()
                        if line:
                            entry = _parse_line(line)
                            if not entry:
                                continue
                            # Push to every subscriber's queue (non-blocking)
                            msg = {"type": "log", **entry}
                            stale: list[WebSocket] = []
                            for ws, q in self._subscribers.items():
                                try:
                                    q.put_nowait(msg)
                                except asyncio.QueueFull:
                                    stale.append(ws)
                            for ws in stale:
                                self.disconnect(ws)
                            continue
                        # No new data — check for rotation
                        try:
                            if os.stat(log_path).st_ino != os.fstat(f.fileno()).st_ino:
                                f.close()
                                f = open(log_path, "r", encoding="utf-8", errors="replace")
                                logger.info("Log file rotated, re-opening")
                        except Exception:
                            pass
                        await asyncio.sleep(0.5)
            except Exception:
                logger.exception("Tailer error, retrying in 2s")
                await asyncio.sleep(2)


manager = ConnectionManager()


@router.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket):
    """Stream log entries in real-time via the single global file tailer.

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
    queue = await manager.connect(websocket)

    try:
        while True:
            msg = await queue.get()
            # Apply per-connection source filter
            if source_filter:
                src = msg.get("source", "")
                if src != source_filter:
                    continue
            text = json.dumps(msg, default=str, ensure_ascii=False)
            await websocket.send_text(text)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WS send loop error")
    finally:
        manager.disconnect(websocket)

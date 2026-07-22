"""WebSocket endpoints — real-time log streaming and dashboard updates."""

import asyncio
import json
import logging
import queue as std_queue
from collections import deque
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.deps import get_current_user_ws
from app.models import LogEntry

router = APIRouter(tags=["websocket"])


class ConnectionManager:
    """Manages active WebSocket connections for real-time updates."""

    def __init__(self) -> None:
        self.active: list[WebSocket] = []
        self._log_buffer: deque[dict[str, Any]] = deque(maxlen=500)
        self._drain_task: asyncio.Task | None = None
        self._drain_logger = logging.getLogger("naspilot.drainer")

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, msg: dict[str, Any]) -> None:
        """Send a message to all connected clients."""
        text = json.dumps(msg, default=str, ensure_ascii=False)
        stale = []
        for ws in self.active:
            try:
                await ws.send_text(text)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)

    async def _send_json(self, ws: WebSocket, msg: dict[str, Any]) -> bool:
        """Send to a single WS; returns False on failure."""
        try:
            await ws.send_text(json.dumps(msg, default=str, ensure_ascii=False))
            return True
        except Exception:
            return False

    async def start_draining(self) -> None:
        """Background task: drain log queue → buffer + broadcast + DB."""
        from app.core.logging_config import get_log_queue

        log_queue = get_log_queue()
        while True:
            try:
                batch: list[dict[str, Any]] = []
                while True:
                    try:
                        batch.append(log_queue.get_nowait())
                    except std_queue.Empty:
                        break

                if batch:
                    self._log_buffer.extend(batch)
                    # Broadcast to ALL connected clients
                    for entry in batch:
                        await self.broadcast({"type": "log", **entry})
                    # Persist to DB
                    try:
                        from datetime import datetime as dt
                        async with async_session_factory() as db:
                            for entry in batch:
                                ts = entry.get("timestamp")
                                if isinstance(ts, str):
                                    try:
                                        ts = dt.fromisoformat(ts)
                                    except (ValueError, TypeError):
                                        ts = dt.now(dt.UTC if hasattr(dt, 'UTC') else None)
                                if ts is None:
                                    ts = dt.now()
                                db.add(LogEntry(
                                    timestamp=ts,
                                    logger=entry["logger"],
                                    level=entry["level"],
                                    source=entry.get("source", "system"),
                                    message=entry["message"],
                                ))
                            await db.commit()
                    except Exception:
                        self._drain_logger.exception("Failed to persist log entries to DB")
            except Exception:
                self._drain_logger.exception("Drainer loop error")
            await asyncio.sleep(0.5)

    def ensure_draining(self) -> None:
        """Start the drain task if not already running (idempotent)."""
        if self._drain_task is None or self._drain_task.done():
            self._drain_task = asyncio.ensure_future(self.start_draining())


manager = ConnectionManager()


@router.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket):
    """Stream log entries in real-time. Requires token via `?token=`.

    Optional query params:
    - ``source`` : filter logs by source (e.g. ``plugin:pt_rss``, ``scheduler``).
    """
    async with async_session_factory() as db:
        user = await get_current_user_ws(websocket, db)
    if not user:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # Extract source filter from query params
    source_filter = websocket.query_params.get("source")

    await manager.connect(websocket)
    manager.ensure_draining()

    # Send recent buffer first (respect filter)
    for entry in manager._log_buffer:
        if source_filter and entry.get("source") != source_filter:
            continue
        if not await manager._send_json(websocket, {"type": "log", **entry}):
            manager.disconnect(websocket)
            return

    try:
        while True:
            await asyncio.sleep(30)
            await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        manager.disconnect(websocket)

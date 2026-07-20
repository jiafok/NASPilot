"""WebSocket endpoints — real-time log streaming and dashboard updates."""

import asyncio
import json
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


manager = ConnectionManager()


@router.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket):
    """Stream log entries in real-time. Requires token via `?token=`."""
    async with async_session_factory() as db:
        user = await get_current_user_ws(websocket, db)
    if not user:
        await websocket.close(code=4001, reason="Unauthorized")
        return
    await manager.connect(websocket)
    # Send recent buffer first
    for entry in manager._log_buffer:
        await websocket.send_text(json.dumps(entry, default=str, ensure_ascii=False))
    try:
        while True:
            await asyncio.sleep(30)
            await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        manager.disconnect(websocket)

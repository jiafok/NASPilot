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
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.database import async_session_factory
from app.core.deps import get_current_user_ws
from app.services.docker_service import DockerExecSession, DockerException, NotFound
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
        return slug  # e.g. "pt_rss" — no "plugin:" prefix
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
        
        # Synchronous function to be run in thread pool
        def _read_log_chunk(path: str, current_inode: int | None) -> tuple[list[dict[str, Any]], int | None]:
            """Read one chunk of log file, return entries and updated inode."""
            if not os.path.isfile(path):
                return [], None
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    if current_inode is None:
                        f.seek(0, os.SEEK_END)  # Start from end on first run
                    try:
                        new_inode = os.fstat(f.fileno()).st_ino
                    except:
                        new_inode = current_inode
                    
                    lines = []
                    for _ in range(1000):  # Read up to 1000 lines per chunk
                        line = f.readline()
                        if not line:
                            break
                        entry = _parse_line(line)
                        if entry:
                            lines.append(entry)
                    
                    # Check for rotation
                    rotated = False
                    try:
                        if current_inode is not None and os.stat(path).st_ino != new_inode:
                            rotated = True
                    except:
                        pass
                    
                    return lines, (new_inode if not rotated else None)
            except Exception:
                return [], current_inode

        current_inode = None
        while True:
            if not os.path.isfile(log_path):
                await asyncio.sleep(2)
                current_inode = None
                continue
            
            try:
                # Run file read in thread to avoid blocking event loop
                entries, current_inode = await asyncio.to_thread(_read_log_chunk, log_path, current_inode)
                
                # Broadcast entries to all subscribers
                for entries_chunk in [entries[i:i+10] for i in range(0, len(entries), 10)]:
                    stale: list[WebSocket] = []
                    for ws, q in self._subscribers.items():
                        for entry in entries_chunk:
                            try:
                                msg = {"type": "log", **entry}
                                q.put_nowait(msg)
                            except asyncio.QueueFull:
                                stale.append(ws)
                                break
                    for ws in stale:
                        self.disconnect(ws)
                
                # Sleep briefly between reads to avoid spinning
                if not entries:
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


@router.websocket("/ws/docker/exec")
async def ws_docker_exec(websocket: WebSocket):
    """Interactive container shell over WebSocket.

    Query params:
    - token: JWT
    - container_id: docker container ID/name
    - user: optional user
    - workdir: optional workdir
    """
    async with async_session_factory() as db:
        user = await get_current_user_ws(websocket, db)
    if not user:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    container_id = websocket.query_params.get("container_id", "").strip()
    exec_user = websocket.query_params.get("user", "").strip()
    workdir = websocket.query_params.get("workdir", "").strip()
    if not container_id:
        await websocket.close(code=4400, reason="container_id is required")
        return

    await websocket.accept()

    try:
        # Wrap session creation in thread to avoid blocking event loop
        session = await asyncio.to_thread(
            lambda: DockerExecSession(
                container_id=container_id,
                user=exec_user or None,
                workdir=workdir or None,
                shell="/bin/sh",
            )
        )
    except NotFound:
        await websocket.send_text(json.dumps({"type": "error", "message": "Container not found"}, ensure_ascii=False))
        await websocket.close(code=4404)
        return
    except DockerException as exc:
        await websocket.send_text(json.dumps({"type": "error", "message": f"Docker unavailable: {exc}"}, ensure_ascii=False))
        await websocket.close(code=4503)
        return
    except Exception as exc:
        await websocket.send_text(json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=False))
        await websocket.close(code=4500)
        return

    await websocket.send_text(json.dumps({"type": "status", "status": "connected"}, ensure_ascii=False))

    stop_event = asyncio.Event()

    async def pump_output() -> None:
        """Read container output and send to frontend with message batching to prevent freeze.
        
        Accumulates multiple reads within a 10ms window to reduce message flood.
        Without this, high-output containers send 100+ messages/sec, causing frontend lag.
        """
        while not stop_event.is_set():
            try:
                # Set a short timeout to enable read batching within that window
                data = await asyncio.wait_for(
                    asyncio.to_thread(session.read, 8192),
                    timeout=0.01  # 10ms window for batching
                )
            except asyncio.TimeoutError:
                # Socket has no data in 10ms; minimal yield and retry
                await asyncio.sleep(0.001)
                continue
            
            if not data:
                break
            
            # Accumulate multiple reads to batch into single message
            buffer = data
            deadline = asyncio.get_event_loop().time() + 0.008  # Collect more for ~8ms
            while asyncio.get_event_loop().time() < deadline and not stop_event.is_set():
                try:
                    chunk = await asyncio.wait_for(
                        asyncio.to_thread(session.read, 8192),
                        timeout=0.002  # Short timeout for supplemental reads
                    )
                    if not chunk:
                        break
                    buffer += chunk
                except asyncio.TimeoutError:
                    break  # No more data in this batch
            
            text = buffer.decode("utf-8", errors="replace")
            await websocket.send_text(json.dumps({"type": "stdout", "data": text}, ensure_ascii=False))
            # Small delay to throttle message rate and allow frontend to process
            await asyncio.sleep(0.001)

    output_task = asyncio.create_task(pump_output())

    try:
        while True:
            incoming = await websocket.receive_text()
            payload: dict[str, Any] | None = None
            try:
                payload = json.loads(incoming)
            except json.JSONDecodeError:
                payload = {"type": "stdin", "data": incoming}

            if payload.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            if payload.get("type") == "stdin":
                session.write(str(payload.get("data") or ""))
                continue

            if payload.get("type") == "raw":
                session.write(str(payload.get("data") or ""))
                continue

            # Fallback: if client sends unknown object payload with a data field
            # treat it as terminal stdin to keep compatibility with simple clients.
            if "data" in payload:
                session.write(str(payload.get("data") or ""))
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Docker exec websocket error")
    finally:
        stop_event.set()
        output_task.cancel()
        with suppress(Exception):
            await output_task
        inspect = {}
        with suppress(Exception):
            inspect = await asyncio.to_thread(session.inspect)
        with suppress(Exception):
            await websocket.send_text(json.dumps({"type": "status", "status": "closed", "exit_code": inspect.get("ExitCode")}, ensure_ascii=False))
        with suppress(Exception):
            await asyncio.to_thread(session.close)

"""Unified logging configuration."""

import logging
import queue
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from app.core.config import settings

LOG_FORMAT = "%(asctime)s [%(levelname)-8s] %(name)s — %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Thread-safe queue shared between DBLogHandler (sync emit) and asyncio drainer
_log_queue: queue.Queue = queue.Queue()


def _extract_source(logger_name: str) -> str:
    """Map a Python logger name to a log source category."""
    if logger_name.startswith("naspilot.plugin."):
        slug = logger_name.replace("naspilot.plugin.", "")
        return f"plugin:{slug}"
    if "scheduler" in logger_name:
        return "scheduler"
    if "task" in logger_name:
        return "task"
    return "system"


class DBLogHandler(logging.Handler):
    """Pushes formatted log entries into a thread-safe queue.

    A background asyncio task (started in lifespan) drains the queue,
    writing to the ``log_entries`` DB table and broadcasting via WebSocket.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "logger": record.name,
                "level": record.levelname,
                "source": _extract_source(record.name),
                "message": self.format(record),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            _log_queue.put(entry)
        except Exception:
            self.handleError(record)


def setup_logging() -> None:
    """Configure root logger with console + rotating file handler."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if settings.DEBUG else logging.INFO)

    # remove existing handlers (uvicorn may add some)
    root.handlers.clear()

    # console
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    root.addHandler(console)

    # file — fallback gracefully if dir is not writable
    try:
        settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            settings.LOG_DIR / "naspilot.log",
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        root.addHandler(file_handler)
    except PermissionError:
        root.warning(f"Cannot write log file at {settings.LOG_DIR} — logging to stdout only")

    # DB + WebSocket broadcast handler
    try:
        db_handler = DBLogHandler()
        db_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        root.addHandler(db_handler)
    except Exception:
        pass  # Don't let log handler init break startup


logger = logging.getLogger("naspilot")

def get_log_queue() -> queue.Queue:
    """Return the global log queue so the drainer can consume it."""
    return _log_queue

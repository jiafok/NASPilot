"""Unified logging configuration."""

import logging
import sys
from logging.handlers import RotatingFileHandler

from app.core.config import settings

LOG_FORMAT = "%(asctime)s [%(levelname)-8s] %(name)s — %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Shared log file path — WebSocket file-tailer and raw-log endpoint read this
LOG_FILE: str = ""


def setup_logging() -> None:
    """Configure root logger with console + rotating file handler."""
    global LOG_FILE

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if settings.DEBUG else logging.INFO)
    root.handlers.clear()

    # console
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    root.addHandler(console)

    # file
    try:
        settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = settings.LOG_DIR / "naspilot.log"
        LOG_FILE = str(log_path)
        file_handler = RotatingFileHandler(
            log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        root.addHandler(file_handler)
    except PermissionError:
        root.warning(f"Cannot write log file at {settings.LOG_DIR} — logging to stdout only")

    # ── Per-subsystem log levels ──
    logging.getLogger("naspilot").setLevel(logging.DEBUG)
    for lib in ("httpx", "httpcore", "apscheduler", "sqlalchemy.engine"):
        logging.getLogger(lib).setLevel(logging.WARNING)


logger = logging.getLogger("naspilot")

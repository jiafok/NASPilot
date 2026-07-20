"""Unified logging configuration."""

import logging
import sys
from logging.handlers import RotatingFileHandler

from app.core.config import settings

LOG_FORMAT = "%(asctime)s [%(levelname)-8s] %(name)s — %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


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

    # file
    file_handler = RotatingFileHandler(
        settings.LOG_DIR / "naspilot.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    root.addHandler(file_handler)


logger = logging.getLogger("naspilot")

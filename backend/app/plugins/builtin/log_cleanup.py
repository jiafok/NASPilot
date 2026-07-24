"""Log Cleanup Plugin — purge old log files and truncate oversized ones.

Ported from cron_log_cleanup.sh:
- Delete log files older than max_age_days
- Truncate files larger than max_size_kb (keep last N lines)
- Logs are file-only (no DB log entries) — DB only stores config.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from app.plugins.registry import PluginBase, PluginMeta

logger = logging.getLogger("naspilot.plugin.log_cleanup")

LOCAL_TZ = timezone(timedelta(hours=8))


def _cleanup_sync(cfg: dict[str, Any]) -> dict[str, Any]:
    log_dir = cfg.get("log_dir", "/app/data/logs").rstrip("/")
    max_age_days = int(cfg.get("max_age_days", 30))
    max_size_kb = int(cfg.get("max_size_kb", 256))
    tail_lines = int(cfg.get("tail_lines", 2000))

    if not os.path.isdir(log_dir):
        return {"status": "ok", "deleted": 0, "truncated": 0, "message": f"log_dir not found: {log_dir}"}

    deleted = 0
    truncated = 0
    cutoff = datetime.now().timestamp() - max_age_days * 86400
    max_bytes = max_size_kb * 1024

    for fname in os.listdir(log_dir):
        if not fname.endswith(".log"):
            continue
        fpath = os.path.join(log_dir, fname)
        if not os.path.isfile(fpath):
            continue

        # Delete old files
        if os.path.getmtime(fpath) < cutoff:
            try:
                os.remove(fpath)
                deleted += 1
                continue
            except OSError:
                pass

        # Truncate large files
        size = os.path.getsize(fpath)
        if size > max_bytes:
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                    lines = fh.readlines()
                keep = lines[-tail_lines:] if len(lines) > tail_lines else lines
                tmp = fpath + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    fh.writelines(keep)
                os.replace(tmp, fpath)
                truncated += 1
            except OSError:
                pass

    return {"status": "ok", "deleted": deleted, "truncated": truncated}


class LogCleanupPlugin(PluginBase):
    META = PluginMeta(
        slug="log_cleanup",
        name="日志清理",
        description="删除过期日志文件、截断超大日志",
        version="1.0.0",
        author="NASPilot",
        icon="🧹",
        category="system",
        entrypoint="app.plugins.builtin.log_cleanup",
    )

    @property
    def default_config(self) -> dict[str, Any]:
        return {
            "log_dir": "/app/data/logs",
            "max_age_days": 30,
            "max_size_kb": 102400,
            "tail_lines": 2000,
        }

    def get_config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "log_dir": {"type": "string", "title": "日志目录"},
                "max_age_days": {"type": "integer", "title": "日志文件保留天数"},
                "max_size_kb": {"type": "integer", "title": "单文件最大大小 (KB)"},
                "tail_lines": {"type": "integer", "title": "截断后保留行数"},
            },
        }

    async def on_enable(self) -> None:
        logger.info("Log Cleanup plugin enabled")

    async def on_disable(self) -> None:
        logger.info("Log Cleanup plugin disabled")

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        logger.info("启动日志清理: dir=%s, max_age=%sd",
                     self.config.get("log_dir","/app/data/logs"),
                     self.config.get("max_age_days",30))
        try:
            file_result = await asyncio.to_thread(_cleanup_sync, self.config)
            return file_result
        except Exception as exc:
            err_msg = str(exc) if str(exc) else type(exc).__name__
            logger.error("Log Cleanup run failed: %s", err_msg)
            return {"status": "error", "error": err_msg[:500], "deleted": 0, "truncated": 0}

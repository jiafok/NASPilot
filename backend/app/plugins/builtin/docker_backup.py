"""Docker App Backup Plugin — exact port of backup_docker_all_core.sh.

Scans /volume1/docker/ for app directories (those containing config/data/conf/db).
Excludes media/downloads/cache/logs/transcode/imagecache.
v2raya gets white-list treatment.
Copies docker-compose*.yml / .env alongside.
Archives everything to a single .tgz.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tarfile
from datetime import datetime, timedelta, timezone
from typing import Any

from app.plugins.registry import PluginBase, PluginMeta

logger = logging.getLogger("naspilot.plugin.docker_backup")

LOCAL_TZ = timezone(timedelta(hours=8))

# Directories that mark an "app" in /volume1/docker/
DATA_DIR_NAMES = ("config", "data", "conf", "db")

# Directories excluded at top level (media/downloads etc.)
EXCLUDED_TOP_DIRS = {"media", "downloads", "download", "movies", "tv", "music"}

# Directories excluded at any depth (cache/tmp/logs etc.)
EXCLUDED_SUBDIR_NAMES = {"cache", "tmp", "temp", "logs", "transcode", "imagecache"}

# v2raya white-list file name prefixes
V2RAYA_WHITELIST_PREFIXES = ("config.json", "subscribe", "routing")

# Compose-related files to always include
COMPOSE_FILES = {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml", ".env"}


def _now_tag() -> str:
    return datetime.now(LOCAL_TZ).strftime("%Y%m%d_%H%M%S")


def _fmt_size(n: int) -> str:
    if n <= 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _is_excluded_dir(name: str) -> bool:
    return name.lower() in EXCLUDED_SUBDIR_NAMES


def _should_skip_root(name: str) -> bool:
    """Check if the root-level subdirectory should be excluded entirely."""
    return name.lower() in EXCLUDED_TOP_DIRS


def _copy_app(src: str, dst: str, app_name: str) -> dict[str, Any]:
    """Copy an app directory tree, pruning excluded paths. Mirrors rsync logic."""
    copied_files = 0
    os.makedirs(dst, exist_ok=True)

    for root, dirs, files in os.walk(src, topdown=True):
        # Prune excluded subdirectories in-place
        dirs[:] = [d for d in dirs if not _is_excluded_dir(d)]

        for fname in files:
            src_file = os.path.join(root, fname)
            # Check if this file's parent path contains an excluded component
            rel = os.path.relpath(src_file, src).replace("\\", "/")
            parts = rel.split("/")

            # Top-level exclusion
            if _should_skip_root(parts[0]):
                continue
            # Any-level exclusion
            if any(_is_excluded_dir(p) for p in parts):
                continue

            target = os.path.join(dst, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            try:
                shutil.copy2(src_file, target)
                copied_files += 1
            except OSError as e:
                logger.warning(f"Copy failed: {src_file} — {e}")

    return {"app": app_name, "files": copied_files}


def _copy_v2raya(src_dir: str, dst_dir: str) -> dict[str, Any]:
    """v2raya white-list — only config.json, subscribe*.json, routing*.json."""
    config_src = os.path.join(src_dir, "config")
    config_dst = os.path.join(dst_dir, "config")
    os.makedirs(config_dst, exist_ok=True)

    if not os.path.isdir(config_src):
        return {"app": "v2raya", "files": 0, "mode": "whitelist"}

    copied = 0
    for fname in os.listdir(config_src):
        full = os.path.join(config_src, fname)
        if not os.path.isfile(full):
            continue
        for prefix in V2RAYA_WHITELIST_PREFIXES:
            if fname.startswith(prefix):
                shutil.copy2(full, os.path.join(config_dst, fname))
                copied += 1
                break

    return {"app": "v2raya", "files": copied, "mode": "whitelist"}


def _backup_sync(cfg: dict[str, Any]) -> dict[str, Any]:
    """Synchronous backup — complete port of backup_docker_all_core.sh."""

    docker_root = cfg.get("docker_root", "/volume1/docker")
    backup_root = cfg.get("backup_dir", "/volumeUSB1/usbshare/docker_backup")
    keep_days = int(cfg.get("keep_days", 7))
    containers_filter: list[str] = cfg.get("containers") or []

    if not os.path.isdir(docker_root):
        return {"status": "failed", "error": f"docker_root not found: {docker_root}"}

    os.makedirs(backup_root, exist_ok=True)

    tag = _now_tag()
    tmp_dir = os.path.join(backup_root, f"docker_all_core_{tag}")
    os.makedirs(tmp_dir, exist_ok=True)

    apps: list[dict[str, Any]] = []
    total_files = 0

    try:
        for entry in sorted(os.listdir(docker_root)):
            app_path = os.path.join(docker_root, entry)
            if not os.path.isdir(app_path):
                continue

            app_name = entry
            if containers_filter and app_name not in containers_filter:
                continue

            # Must have at least one config/data/conf/db subdirectory
            has_data = any(os.path.isdir(os.path.join(app_path, d)) for d in DATA_DIR_NAMES)
            if not has_data:
                logger.info(f"Skipping (no core data): {app_name}")
                continue

            app_dest = os.path.join(tmp_dir, app_name)
            logger.info(f"Collecting: {app_name}")

            if app_name == "v2raya":
                result = _copy_v2raya(app_path, app_dest)
            else:
                result = _copy_app(app_path, app_dest, app_name)

            # Copy compose / .env files
            for fname in COMPOSE_FILES:
                src_file = os.path.join(app_path, fname)
                if os.path.isfile(src_file):
                    shutil.copy2(src_file, os.path.join(app_dest, fname))

            apps.append(result)
            total_files += result["files"]

        # Archive to .tgz
        archive_path = os.path.join(backup_root, f"docker_all_core_{tag}.tgz")
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(tmp_dir, arcname=f"docker_all_core_{tag}")
        archive_size = os.path.getsize(archive_path)

        # Prune old backups
        pruned = 0
        if keep_days > 0:
            cutoff = datetime.now().timestamp() - keep_days * 86400
            for fname in os.listdir(backup_root):
                fpath = os.path.join(backup_root, fname)
                if not fname.startswith("docker_all_core_"):
                    continue
                try:
                    if os.path.getmtime(fpath) < cutoff:
                        if os.path.isdir(fpath):
                            shutil.rmtree(fpath)
                        else:
                            os.remove(fpath)
                        pruned += 1
                except OSError:
                    pass

        return {
            "status": "ok",
            "archive": archive_path,
            "archive_size": _fmt_size(archive_size),
            "apps": [a["app"] for a in apps],
            "apps_count": len(apps),
            "total_files": total_files,
            "pruned_old": pruned,
            "tag": tag,
        }

    finally:
        if os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


class DockerBackupPlugin(PluginBase):
    META = PluginMeta(
        slug="docker_backup",
        name="Docker App Backup",
        description="Backup /volume1/docker/ app configs (no media/cache/logs). Exact port of backup_docker_all_core.sh.",
        version="3.0.0",
        category="system",
    )

    async def on_enable(self) -> None:
        pass

    async def on_disable(self) -> None:
        pass

    async def run(self, **kwargs: Any) -> Any:
        result = await asyncio.to_thread(_backup_sync, self.config)
        history: list[dict[str, Any]] = self.config.setdefault("state", {}).setdefault("history", [])
        history.insert(0, result)
        self.config["state"]["history"] = history[:30]

        if result.get("status") == "ok":
            await self.notify(
                "Docker Backup Complete",
                f"Archive: {os.path.basename(result.get('archive', '?'))}\n"
                f"Size: {result.get('archive_size', '?')}\n"
                f"Apps: {result.get('apps_count', 0)} — {', '.join(result.get('apps', []))}\n"
                f"Files: {result.get('total_files', 0)}",
                level="info",
            )

        return result
